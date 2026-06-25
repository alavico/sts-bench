"""Replay a recorded run into the live game and stop at a chosen point.

    uv run python -m sts_bench.drive <trajectory.jsonl> --until-decision 47
    uv run python -m sts_bench.drive <trajectory.jsonl> --until-floor 14

Start this first (it listens on the relay port), then trigger
CommunicationMod's external process in-game -- same handshake as `play`. The
driver issues the trajectory's recorded wire commands in order, with no model
and no agent in the loop, until it reaches the state you asked for, then holds
the game there so you can screenshot it (Ctrl-C to release).

The unit of replay is the recorded `command` per decision -- the exact string
that went over the wire the first time, forced fallbacks included -- so a fixed
seed reproduces the same states. `--until-decision N` lands on the state
decision N *faced* (it replays decisions 1..N-1 and stops); `--inclusive` also
plays N. `--until-floor F` stops at the start of floor F. Pick the number with
`sts_bench.replay <file>` first -- it prints every decision and floor.

At each floor boundary the live game_state is checked against the recorded
entry_state: a clean match all the way down is proof the replay is faithful;
a mismatch flags nondeterminism (or a skipped unparsed-state step) before you
trust the screenshot.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .cli import add_env_file_arg, apply_env_file
from .env import CommunicationModEnv, HarnessServer, StepResult
from .protocol_log import ProtocolLog, model_traffic
from .smoke import choose_command, session_dir
from .trajectory import DecisionRecord, FloorRecord, RunRecord, read_records

# Scalar fields compared at each floor boundary; deck and relics are compared
# alongside them (see entry_diffs) -- a clean match the whole way down is proof
# the replay is faithful, and the first floor that diverges localizes a desync.
VERIFY_FIELDS = ("floor", "current_hp", "max_hp", "gold", "screen_type")
MENU_STEP_CAP = 50  # generous: menu -> character select -> start is a handful
# Live play paced commands by model/API latency, which let the game settle
# between them; replay fires as fast as the env returns ready, which can land a
# command on a transiently-ready combat state before the draw resolves. A small
# delay restores that breathing room. Tune with --delay (0 to disable).
DEFAULT_DELAY = 0.5


@dataclass(frozen=True)
class ReplayPlan:
    """What to send and where to stop, derived from a trajectory alone."""

    seed: str | None
    character: str
    ascension: int
    steps: list[DecisionRecord]  # in-range decisions with a recorded command, in order
    target_index: int  # decision we stop at (faced unless inclusive)
    target_floor: int | None
    target_decision: DecisionRecord | None
    floor_entries: dict[int, dict] = field(default_factory=dict)
    unparsed_states: int = 0
    missing_commands: int = 0  # in-range decisions with no recorded command

    @property
    def commands(self) -> list[str]:
        return [s.command for s in self.steps if s.command is not None]


class PlanError(Exception):
    """The trajectory can't be turned into a replay plan."""


def plan_replay(
    records: list,
    *,
    until_decision: int | None = None,
    until_floor: int | None = None,
    inclusive: bool = False,
) -> ReplayPlan:
    """Turn a record stream into an ordered command list and a stop point.

    Exactly one of `until_decision` / `until_floor` selects the target. Commands
    are the recorded wire strings for every decision strictly before the target
    (through it, when `inclusive`).
    """
    if (until_decision is None) == (until_floor is None):
        raise PlanError("pass exactly one of until_decision / until_floor")

    run = next((r for r in records if isinstance(r, RunRecord)), None)
    if run is None:
        raise PlanError(
            "no run record in this trajectory (the run died before it was written); "
            "the seed and character can't be recovered, so it can't be driven"
        )
    decisions = sorted(
        (r for r in records if isinstance(r, DecisionRecord)),
        key=lambda d: d.decision_index,
    )
    if not decisions:
        raise PlanError("trajectory has no decisions to replay")
    floor_entries = {
        r.floor: r.entry_state
        for r in records
        if isinstance(r, FloorRecord) and r.entry_state
    }

    if until_decision is not None:
        target_index = until_decision
        lo, hi = decisions[0].decision_index, decisions[-1].decision_index
        if not lo <= target_index <= hi:
            raise PlanError(
                f"--until-decision {target_index} is out of range "
                f"(this run has decisions {lo}..{hi})"
            )
    else:
        on_floor = [d for d in decisions if d.floor == until_floor]
        if not on_floor:
            reached = sorted({d.floor for d in decisions if d.floor is not None})
            raise PlanError(
                f"no decision recorded on floor {until_floor} "
                f"(floors with decisions: {reached})"
            )
        target_index = on_floor[0].decision_index

    cutoff = target_index + 1 if inclusive else target_index
    in_range = [d for d in decisions if d.decision_index < cutoff]
    steps = [d for d in in_range if d.command is not None]
    missing = sum(1 for d in in_range if d.command is None)
    target_decision = next(
        (d for d in decisions if d.decision_index == target_index), None
    )

    return ReplayPlan(
        seed=run.seed,
        character=run.character,
        ascension=run.ascension,
        steps=steps,
        target_index=target_index,
        target_floor=target_decision.floor if target_decision else until_floor,
        target_decision=target_decision,
        floor_entries=floor_entries,
        unparsed_states=run.totals.unparsed_states,
        missing_commands=missing,
    )


def _card_names(cards: list | None) -> list[str]:
    """Sorted card labels (name, with a + for upgrades) -- a deck as a multiset."""
    return sorted(
        f"{c.get('name', '?')}{'+' if c.get('upgrades') else ''}" for c in (cards or [])
    )


def _names(items: list | None) -> list[str]:
    return sorted(i.get("name", "?") for i in (items or []))


def _multiset_delta(recorded: list[str], live: list[str]) -> str:
    """`-missing +extra` between two label multisets, for a one-line report."""
    from collections import Counter

    rc, lc = Counter(recorded), Counter(live)
    missing = sorted((rc - lc).elements())
    extra = sorted((lc - rc).elements())
    parts = []
    if missing:
        parts.append("-" + ",".join(missing))
    if extra:
        parts.append("+" + ",".join(extra))
    return " ".join(parts)


def entry_diffs(live: dict, recorded: dict) -> list[str]:
    """How a live floor-entry state differs from the recorded one.

    Empty means the replay is on track. Beyond the scalar vitals it compares
    the deck and relics, so a desync surfaces at the floor it first appears
    (a wrong card or missing relic) instead of waiting for a command to be
    rejected several decisions later.
    """
    diffs = [
        f"{f} {recorded.get(f)!r}->{live.get(f)!r}"
        for f in VERIFY_FIELDS
        if recorded.get(f) != live.get(f)
    ]
    rec_deck, live_deck = _card_names(recorded.get("deck")), _card_names(live.get("deck"))
    if rec_deck != live_deck:
        delta = _multiset_delta(rec_deck, live_deck)
        diffs.append(f"deck {len(rec_deck)}->{len(live_deck)} ({delta})")
    rec_relics, live_relics = _names(recorded.get("relics")), _names(live.get("relics"))
    if rec_relics != live_relics:
        diffs.append(f"relics ({_multiset_delta(rec_relics, live_relics)})")
    return diffs


def command_divergence(decision: DecisionRecord, game_state: dict) -> str | None:
    """A confirmed mismatch between a decision's recorded action and the live
    state at the index its command targets, or None.

    The recorded action names the card or option it took (`play_card 4 (Cleave)`,
    `choose 1 (cleave)`, `use_potion 0 (Attack Potion)`); the live state must
    hold that same thing at that index. When it doesn't, the run's RNG has
    drifted -- typically an upstream random card/potion effect (Attack Potion,
    Entropic Brew) that doesn't reproduce from the command stream alone. This
    catches the drift at the decision it first bites, even when the wrong card
    is still playable and the game would not reject the command.

    Only positive, in-range mismatches are reported; anything ambiguous returns
    None so the check never invents a divergence.
    """
    action, command = decision.action, decision.command or ""
    if not action or not command:
        return None
    m = re.search(r"\(([^)]+)\)", action)
    if not m:
        return None
    recorded = m.group(1).strip().lower()
    if recorded == "skip":  # the skip option isn't an indexable card/choice
        return None
    parts = command.split()
    if len(parts) < 2:
        return None
    head = parts[0]
    combat = game_state.get("combat_state") or {}
    if head == "play" and parts[1].isdigit():
        i = int(parts[1]) - 1  # PLAY hand index is 1-based
        hand = combat.get("hand") or []
        if 0 <= i < len(hand):
            live = (hand[i].get("name") or "").lower()
            if live and live != recorded:
                return f"play hand[{i}]: recorded {recorded!r}, live has {live!r}"
    elif head == "choose" and parts[1].isdigit():
        i = int(parts[1])
        choices = game_state.get("choice_list") or []
        if 0 <= i < len(choices):
            live = (choices[i] or "").lower()
            if live and live != recorded:
                return f"choose {i}: recorded {recorded!r}, live offers {live!r}"
    elif head == "potion" and len(parts) >= 3 and parts[1] == "use" and parts[2].isdigit():
        i = int(parts[2])
        potions = game_state.get("potions") or []
        if 0 <= i < len(potions):
            live = (potions[i].get("name") or "").lower()
            if live and live != recorded:
                return f"potion[{i}]: recorded {recorded!r}, live has {live!r}"
    return None


def _print_target_view(plan: ReplayPlan, records: list, emit) -> None:
    """Show what the model was sent at the target decision -- the digest column
    of the blog triptych, alongside the live game you're about to screenshot."""
    decision = plan.target_decision
    if decision is None:
        return
    floor = next(
        (
            r
            for r in records
            if isinstance(r, FloorRecord) and r.floor == decision.floor
        ),
        None,
    )
    if floor is None:
        return
    slice_ = floor.conversation[decision.message_start : decision.message_end]
    emit(f"--- what decision {decision.decision_index} sent to the model ---")
    for tag, text in model_traffic(slice_):
        for line in text.splitlines():
            emit(f"{tag} {line}")


def run(args: argparse.Namespace) -> int:
    def say(msg: str) -> None:
        print(f"[drive] {msg}", file=sys.stderr)

    path = Path(args.path)
    if not path.exists():
        say(f"no such file: {path}")
        return 1
    if not apply_env_file(args.env_file, say):
        return 1

    records = list(read_records(path))
    try:
        plan = plan_replay(
            records,
            until_decision=args.until_decision,
            until_floor=args.until_floor,
            inclusive=args.inclusive,
        )
    except PlanError as exc:
        say(str(exc))
        return 1

    landing = (
        f"after decision {plan.target_index}"
        if args.inclusive
        else f"the state decision {plan.target_index} faced"
    )
    say(
        f"plan: seed {plan.seed or 'random'}, {plan.character} ascension {plan.ascension}; "
        f"{len(plan.commands)} commands to reach {landing} (floor {plan.target_floor})"
    )
    if plan.unparsed_states:
        say(
            f"WARNING: the run had {plan.unparsed_states} unparsed state(s); their "
            "scripted steps are not individually recorded, so replay may desync"
        )
    if plan.missing_commands:
        say(
            f"WARNING: {plan.missing_commands} in-range decision(s) had no recorded "
            "command; replay will skip them and may desync"
        )
    if plan.seed is None:
        say("WARNING: this run rolled its own seed; the live game cannot reproduce it")

    log: ProtocolLog | None = None
    try:
        with HarnessServer(port=args.port) as server:
            say(f"listening on {server.host}:{server.port} -- start the external process in-game")
            conn = server.accept(timeout=300)
            log = ProtocolLog(session_dir(), name="drive")
            say(f"protocol log: {log.path}")
            env = CommunicationModEnv(conn, on_protocol_line=log.line)
            state = env.handshake()

            # Menu phase: reproduce exactly how the original run reached the
            # game -- choose_command issues `start <char> <asc> <seed>` once the
            # menu offers it, the same path play_run took out of game.
            menu_steps = 0
            while not state.get("in_game"):
                if menu_steps >= MENU_STEP_CAP:
                    say(f"still at the menu after {MENU_STEP_CAP} steps; giving up")
                    return 1
                command = choose_command(state, plan.character, plan.ascension, plan.seed)
                result = env.step(command)
                if not result.ok:
                    say(f"menu step {command!r} rejected: {result.error}; stopping")
                    return 1
                state = result.state
                menu_steps += 1
            say("in game; replaying recorded commands")

            # Replay phase: send each recorded command verbatim. Before sending,
            # confirm the live state still holds the card/option the recorded
            # action named (catches RNG drift at its source decision); after,
            # verify each floor boundary against the recorded entry_state.
            verified_floors: set[int] = set()
            seen_floor = (state.get("game_state") or {}).get("floor")
            total = len(plan.steps)
            for n, step in enumerate(plan.steps, start=1):
                command = step.command
                assert command is not None  # plan.steps filtered to recorded commands
                drift = command_divergence(step, state.get("game_state") or {})
                if drift:
                    say(
                        f"decision {step.decision_index} (floor {step.floor}): REPLAY DIVERGED -- "
                        f"{drift}. The run's RNG drifted from the record (usually an upstream "
                        "random card/potion effect that doesn't reproduce from commands alone); "
                        "replay can't continue faithfully past here."
                    )
                    return 1
                result: StepResult = env.step(command)
                if not result.ok:
                    say(
                        f"command {n}/{total} {command!r} rejected: "
                        f"{result.error} -- the live game diverged from the record; stopping"
                    )
                    return 1
                state = result.state
                game_state = state.get("game_state") or {}
                floor = game_state.get("floor")
                if floor != seen_floor and floor in plan.floor_entries and floor not in verified_floors:
                    diffs = entry_diffs(game_state, plan.floor_entries[floor])
                    if diffs:
                        say(f"floor {floor} boundary MISMATCH: {'; '.join(diffs)}")
                    else:
                        say(
                            f"floor {floor} boundary verified "
                            f"(hp {game_state.get('current_hp')}, gold {game_state.get('gold')}, "
                            f"deck {len(game_state.get('deck') or [])})"
                        )
                    verified_floors.add(floor)
                seen_floor = floor
                if args.delay:
                    time.sleep(args.delay)

            game_state = state.get("game_state") or {}
            say(
                f"arrived: floor {game_state.get('floor')} act {game_state.get('act')}, "
                f"HP {game_state.get('current_hp')}/{game_state.get('max_hp')}, "
                f"screen {game_state.get('screen_type')}"
            )
            _print_target_view(plan, records, say)
            say("game is holding at this state -- take your screenshot; Ctrl-C to release")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                say("releasing")
    finally:
        if log is not None:
            say(f"wire trace: {log.path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="trajectory JSONL file (logs/<date>/trajectories/*.jsonl)")
    stop = parser.add_mutually_exclusive_group(required=True)
    stop.add_argument(
        "--until-decision",
        type=int,
        default=None,
        metavar="N",
        help="stop at the state decision N faced (replays decisions before N)",
    )
    stop.add_argument(
        "--until-floor",
        type=int,
        default=None,
        metavar="F",
        help="stop at the start of floor F",
    )
    parser.add_argument(
        "--inclusive",
        action="store_true",
        help="also execute the target decision (land on the state it produced)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        metavar="SECONDS",
        help=f"pause between commands so the game settles (default {DEFAULT_DELAY}; 0 to disable)",
    )
    parser.add_argument("--port", type=int, default=9999)
    add_env_file_arg(parser)
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
