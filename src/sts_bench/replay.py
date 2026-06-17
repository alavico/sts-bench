"""Re-render a logged run from its trajectory JSONL alone.

The trajectory file is the run's complete record: every floor's conversation,
every decision's slice of it, the boundary states, scorecards, and rewards.
This replays one without the game, the model, or the protocol log --
the same `>m`/`<m` channel the live log shows, reconstructed from storage.

    uv run python -m sts_bench.replay logs/2026-06-11/trajectories/play-20260611-101500.jsonl
    uv run python -m sts_bench.replay <file> --floor 14

Every replay also verifies the packet property on every floor: the decision
records' message ranges must tile the floor's stored conversation exactly --
each message owned by one decision, none stored twice, none orphaned. A
violation means the store broke its contract; it renders anyway, flags the
floor, and exits nonzero.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from .protocol_log import model_traffic, reasoning_note
from .trajectory import DecisionRecord, FloorRecord, RunRecord, read_records
from .trajectory.schema import TokenUsage


def group(
    records: list,
) -> tuple[RunRecord | None, list[FloorRecord], dict[int | None, list[DecisionRecord]]]:
    """Split a record stream into the run record (None for a run that died
    mid-flight), floors in played order, and decisions keyed by floor."""
    run = next((r for r in records if isinstance(r, RunRecord)), None)
    floors = [r for r in records if isinstance(r, FloorRecord)]
    decisions: dict[int | None, list[DecisionRecord]] = defaultdict(list)
    for record in records:
        if isinstance(record, DecisionRecord):
            decisions[record.floor].append(record)
    return run, floors, decisions


def verify_floor(floor: FloorRecord, decisions: list[DecisionRecord]) -> list[str]:
    """Packet-property violations for one floor; empty means the contract held.

    The decisions' [start, end) ranges, in decision order, must cover the
    conversation from 0 to its length with no gap and no overlap.
    """
    problems = []
    expected = 0
    for decision in decisions:
        if decision.message_start != expected:
            problems.append(
                f"decision {decision.decision_index}: messages "
                f"[{decision.message_start}, {decision.message_end}) "
                f"{'overlap' if decision.message_start < expected else 'leave a gap'} "
                f"(previous decision ended at {expected})"
            )
        expected = decision.message_end
    if expected != len(floor.conversation):
        problems.append(
            f"conversation has {len(floor.conversation)} messages but decisions "
            f"cover {expected}: messages owned by no decision"
        )
    return problems


def _spend(usage: TokenUsage) -> str:
    cache = f", {usage.cache_read_tokens} cached" if usage.cache_read_tokens else ""
    return f"tokens {usage.prompt_tokens}+{usage.completion_tokens}{reasoning_note(usage)}{cache}"


def _floor_header(floor: FloorRecord, decisions: list[DecisionRecord]) -> str:
    card = floor.scorecard
    gains = ", ".join(
        card.cards_gained + card.relics_gained + card.potions_gained
    )
    parts = [
        f"floor {floor.floor} ({floor.floor_type or '?'})",
        f"HP {floor.entry.hp}->{floor.exit.hp}, gold {floor.entry.gold}->{floor.exit.gold}",
        f"{len(decisions)} decisions",
    ]
    if card.combat_turns:
        parts.append(f"{card.combat_turns} combat turns")
    if gains:
        parts.append(f"gained {gains}")
    if floor.reward is not None:
        components = ", ".join(f"{k} {v:+g}" for k, v in floor.reward.components.items())
        parts.append(f"reward {floor.reward.spec_version} {floor.reward.total:+g}"
                     + (f" ({components})" if components else ""))
    return " | ".join(parts)


def _decision_line(decision: DecisionRecord) -> str:
    what = decision.action or f"{decision.command!r}"
    forced = f" FORCED ({decision.forced_reason}) ->" if decision.forced_reason else ""
    latency = f", {decision.latency_ms}ms" if decision.latency_ms is not None else ""
    return (
        f"decision {decision.decision_index} ({decision.screen}):{forced} {what} "
        f"[rounds {decision.rounds}, lookups {decision.observation_calls}, "
        f"invalid {decision.invalid_actions}{latency}, {_spend(decision.usage)}]"
    )


def render_run(records: list, only_floor: int | None = None) -> Iterator[str]:
    """The whole run as text, one line at a time, protocol-log style."""
    run, floors, decisions = group(records)

    if run is not None:
        effort = f", effort {run.reasoning_effort}" if run.reasoning_effort else ""
        yield (
            f"run {run.run_id} | {run.model} @ {run.provider_base_url} ({run.api} api{effort}) | "
            f"{run.agent} agent | {run.character} ascension {run.ascension} seed {run.seed or 'random'}"
        )
        outcome = run.outcome
        verdict = {True: "VICTORY", False: "DEFEAT"}.get(outcome.victory, "UNFINISHED")
        yield (
            f"outcome: {verdict} on floor {outcome.floor_reached}, score {outcome.score} | "
            f"{run.totals.decisions} decisions, {run.totals.forced} forced, "
            f"{run.totals.unparsed_states} unparsed, {_spend(run.totals.usage)}"
        )
    else:
        yield "no run record: the run died mid-flight; floors below are what completed"

    shown_system = False
    for floor in floors:
        if only_floor is not None and floor.floor != only_floor:
            continue
        if not shown_system and floor.conversation and floor.conversation[0].get("role") == "system":
            yield "system prompt (constant; shown once):"
            for line in (floor.conversation[0].get("content") or "").splitlines():
                yield f">m {line}"
            shown_system = True

        floor_decisions = decisions.get(floor.floor, [])
        yield ""
        yield f"=== {_floor_header(floor, floor_decisions)} ==="
        for problem in verify_floor(floor, floor_decisions):
            yield f"!! packet violation: {problem}"
        for decision in floor_decisions:
            yield f"-- {_decision_line(decision)}"
            slice_ = floor.conversation[decision.message_start : decision.message_end]
            for tag, text in model_traffic(slice_):
                for line in text.splitlines():
                    yield f"{tag} {line}"

    orphans = sorted(
        f for f in decisions if f not in {fl.floor for fl in floors} and f is not None
    )
    for floor_no in orphans:
        yield ""
        yield (
            f"!! floor {floor_no}: {len(decisions[floor_no])} decisions but no floor record "
            f"(run died mid-floor; their conversation slices are unrecoverable)"
        )


def verify(records: list) -> list[str]:
    """All packet-property violations in a record stream."""
    _, floors, decisions = group(records)
    problems = []
    for floor in floors:
        for problem in verify_floor(floor, decisions.get(floor.floor, [])):
            problems.append(f"floor {floor.floor}: {problem}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="trajectory JSONL file (logs/<date>/trajectories/*.jsonl)")
    parser.add_argument("--floor", type=int, default=None, help="only render this floor")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        raise SystemExit(1)

    records = list(read_records(path))
    for line in render_run(records, only_floor=args.floor):
        print(line)

    problems = verify(records)
    if problems:
        print(f"\npacket property VIOLATED ({len(problems)} problems)", file=sys.stderr)
        raise SystemExit(1)
    _, floors, decisions = group(records)
    total_decisions = sum(len(d) for d in decisions.values())
    print(
        f"\npacket property verified: {len(floors)} floors, {total_decisions} decisions, "
        f"every message stored once",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
