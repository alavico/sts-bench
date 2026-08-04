"""One run, played end to end by an agent through a connected game.

Every front end drives runs through this same loop -- the single-run play
CLI and the job-queue runner differ only in setup. Per decision: parse the state
(unparsed states fall back to scripted raw play and get captured), ask the
agent, guard against screen-cycle loops, translate, step, and record. The
scripted smoke policy stays underneath as the safety net, and every fallback
is counted -- a high forced-action count means the result measures the
harness, not the model.

The caller owns everything around the run: server, connection, protocol log,
trajectory recorder, and the agent itself. The tally is caller-allocated and
mutated in place, so a run that dies mid-step still reports its partial
progress from the caller's `finally`.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from ..actions import Action, card_reward_skip_index, translate
from ..agents.heuristic import scripted_action
from ..env import CommunicationModEnv, RawState, StepResult
from ..protocol_log import ProtocolLog, model_traffic, reasoning_note
from ..providers import Usage
from ..smoke import MAX_STEPS, choose_command, dump_unparsed, fallback_command
from ..state import StateMessage, StateParseError, parse_message
from ..trajectory import RunRecorder, screen_label

MAX_DECISIONS = 1000
LOOP_GUARD_LIMIT = 3


@dataclass
class RunTally:
    """A run's running totals, owned by the caller so a crash still reports."""

    decisions: int = 0
    forced: int = 0
    unparsed: int = 0
    usage: Usage = field(default_factory=Usage)
    end_state: RawState | None = None
    # aborted: the loop stopped without the game reaching its natural end
    # (budget hit, dead-end screen). fatal: the wire itself is wedged -- a
    # command and its fallback both failed -- so no further run can start.
    aborted: str | None = None
    fatal: bool = False


class LoopGuard:
    """Trips when the same action keeps being chosen from an unchanged game state.

    A stateless agent on a byte-identical state gives a near-identical answer,
    so any screen cycle that doesn't change the world (shop room <-> shop
    screen, map <-> return) loops forever. Counting (state digest, command)
    pairs over the whole run catches such cycles regardless of period; a
    legitimate repeat (playing Strike twice) never trips because the state
    moved in between.
    """

    def __init__(self, limit: int = LOOP_GUARD_LIMIT):
        self._limit = limit
        self._seen: Counter[tuple[str, str]] = Counter()

    def trips(self, game_state: dict, command: str) -> bool:
        digest = hashlib.sha256(
            json.dumps(game_state, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self._seen[digest, command] += 1
        return self._seen[digest, command] >= self._limit


def describe_action(action: Action, message: StateMessage) -> str:
    """Render an executed action in the model's own terms (0-based, named).

    Used for the agent's outcome reports, so it must match how the model
    expressed the action -- never the wire command (PLAY is 1-based).
    """
    state = message.game_state
    match action.kind:
        case "play_card":
            combat = state.combat_state if state else None
            card = combat.hand[action.card_index].name if combat else "?"
            target = (
                f" -> {combat.monsters[action.target_index].name} [{action.target_index}]"
                if combat and action.target_index is not None
                else ""
            )
            return f"play_card {action.card_index} ({card}){target}"
        case "choose":
            if action.choice_index == card_reward_skip_index(message):
                return f"choose {action.choice_index} (skip)"
            choices = state.choice_list if state else []
            label = f" ({choices[action.choice_index]})" if action.choice_index < len(choices) else ""
            return f"choose {action.choice_index}{label}"
        case "use_potion" | "discard_potion":
            potions = state.potions if state else []
            label = f" ({potions[action.slot_index].name})" if action.slot_index < len(potions) else ""
            return f"{action.kind} {action.slot_index}{label}"
        case "proceed" | "return_back":
            # These reach the wire as whichever alias the screen offers
            # (proceed/confirm, return/skip/cancel/leave); show the verb.
            try:
                wire = translate(action, message)
            except ValueError:  # narration must not crash on an illegal action
                return action.kind
            return action.kind if wire == action.kind else f"{action.kind} ({wire})"
        case _:
            return action.kind


def _floor_summary(entry: dict, exit_state: dict, decisions: int, usage: Usage) -> str:
    """One line per finished floor: the spend and the damage, reported at the
    boundary -- the first point where the floor's result is observable."""
    return (
        f"floor {entry.get('floor')} summary: {decisions} decisions, "
        f"tokens {usage.prompt_tokens}+{usage.completion_tokens}{reasoning_note(usage)}, "
        f"HP {entry.get('current_hp')}->{exit_state.get('current_hp')}, "
        f"gold {entry.get('gold')}->{exit_state.get('gold')}"
    )


def forced_command(
    message: StateMessage, raw_state: RawState, reason: str | None
) -> tuple[str | None, str | None]:
    """(command, history) for a decision the agent failed to make itself.

    A budget exhaustion (invalid actions, rounds) gets the scripted policy --
    a sensible move the model failed to find. A loop-guard trip must not:
    the scripted policy can *be* the loop (observed live: a broke shop's
    leave -> shop-room choose -> re-enter cycle, where the scripted fallback
    re-chose the cycle's own moves), so it gets the raw advance family
    (end/proceed/leave/...) instead -- the one guaranteed to change screens.
    """
    if reason != "loop guard":
        action = scripted_action(message)
        if action is not None:
            return translate(action, message), f"{describe_action(action, message)} (forced)"
    command = fallback_command(raw_state)
    if command is not None:
        return command, f"{command} (forced)"
    return None, None


def _transition_notes(prev: dict, cur: dict) -> list[str]:
    """Run landmarks worth narrating when the game state moves between steps."""
    notes = []
    if not cur:
        return notes
    if cur.get("floor") != prev.get("floor"):
        notes.append(f"entering floor {cur.get('floor')} (act {cur.get('act')}): {screen_label(cur)}")
    prev_combat = prev.get("combat_state") or {}
    cur_combat = cur.get("combat_state") or {}
    if cur_combat and not prev_combat:
        monsters = ", ".join(
            m.get("name", "?") for m in cur_combat.get("monsters", []) if not m.get("is_gone")
        )
        notes.append(f"combat starts vs {monsters}")
    elif prev_combat and not cur_combat:
        notes.append("combat over")
    elif cur_combat and cur_combat.get("turn") != prev_combat.get("turn"):
        notes.append(f"combat turn {cur_combat.get('turn')}")
    return notes


def play_run(
    env: CommunicationModEnv,
    state: RawState,
    agent,
    recorder: RunRecorder,
    *,
    character: str,
    ascension: int,
    seed: str | None,
    log: ProtocolLog,
    say: Callable[[str], None],
    tally: RunTally,
    max_decisions: int = MAX_DECISIONS,
    max_steps: int = MAX_STEPS,
) -> RawState:
    """Play from `state` until the game leaves the run (or a budget/dead end).

    `state` may be out of game: the scripted raw path then issues `start`,
    so a fresh menu state begins the run and the post-game menu state ends it.
    """
    guard = LoopGuard()
    prev_game_state: dict = {}
    floor_entry: dict = {}
    floor_decisions = 0
    floor_usage = Usage()

    for _ in range(max_steps):
        game_state = state.get("game_state") or {}
        if game_state and game_state.get("floor") != floor_entry.get("floor"):
            if floor_entry:
                say(_floor_summary(floor_entry, prev_game_state, floor_decisions, floor_usage))
            floor_entry, floor_decisions, floor_usage = game_state, 0, Usage()
        recorder.observe(game_state)
        for note in _transition_notes(prev_game_state, game_state):
            say(note)
        prev_game_state = game_state
        command = None
        history = None  # what to tell the agent actually happened here
        decision = None
        latency_ms = None

        try:
            message = parse_message(state)
        except StateParseError as exc:
            tally.unparsed += 1
            recorder.unparsed_state()
            path = dump_unparsed(exc.raw)
            say(f"state didn't fit the schema; captured -> {path} (scripted fallback)")
            message = None

        if message is not None and "start" not in message.available_commands:
            if tally.decisions >= max_decisions:
                tally.aborted = f"hit max_decisions={max_decisions}"
                say(f"{tally.aborted}; stopping")
                break
            tally.decisions += 1
            floor_decisions += 1
            started = time.monotonic()
            decision = agent.decide(message)
            latency_ms = int((time.monotonic() - started) * 1000)
            tally.usage += decision.usage
            floor_usage += decision.usage
            for tag, text in model_traffic(decision.transcript):
                for content_line in text.splitlines():
                    log.line(tag, content_line)
            action = decision.action
            if action is not None:
                command = translate(action, message)
                if guard.trips(game_state, command):
                    say(
                        f"decision {tally.decisions}: loop guard -- "
                        f"{describe_action(action, message)} chosen {LOOP_GUARD_LIMIT}x "
                        f"from an unchanged state; forcing scripted fallback"
                    )
                    decision.forced_reason = "loop guard"
                    action = command = None
            if action is not None:
                history = describe_action(action, message)
                say(
                    f"decision {tally.decisions} ({screen_label(game_state)}): {history} "
                    f"[rounds {decision.rounds}, lookups {decision.observation_calls}, "
                    f"invalid {decision.invalid_actions}, "
                    f"tokens {decision.usage.prompt_tokens}+{decision.usage.completion_tokens}"
                    f"{reasoning_note(decision.usage)}]"
                )
            else:
                tally.forced += 1
                command, history = forced_command(message, state, decision.forced_reason)
                say(f"decision {tally.decisions}: FORCED ({decision.forced_reason}) -> {history or command!r}")

        if command is None:
            command = choose_command(state, character, ascension, seed)
            if state.get("in_game"):
                history = f"{command} (scripted)"

        if decision is not None:
            # Recorded before the step: a wire failure right after
            # must not cost the floor packet its final decision.
            recorder.decision(
                decision,
                screen=screen_label(game_state),
                action=history if decision.forced_reason is None else None,
                command=command,
                latency_ms=latency_ms,
            )

        result: StepResult = env.step(command)
        if not result.ok:
            say(f"step {command!r} failed: {result.error}")
            retry = fallback_command(result.state)
            if retry is None:
                tally.aborted = f"step {command!r} failed and no fallback available"
                tally.fatal = True
                say(f"{tally.aborted}; stopping")
                break
            result = env.step(retry)
            if not result.ok:
                tally.aborted = f"fallback {retry!r} failed after {command!r}: {result.error}"
                tally.fatal = True
                say(f"{tally.aborted}; stopping")
                break
            history = f"{retry} (fallback after {command!r} failed)"
        state = result.state

        if history is not None:
            agent.record(
                f"floor {game_state.get('floor')} {screen_label(game_state)}: {history}"
            )

        new_game_state = state.get("game_state") or {}
        if new_game_state.get("screen_type") == "GAME_OVER":
            screen = new_game_state.get("screen_state") or {}
            say(f"run over: {'VICTORY' if screen.get('victory') else 'DEFEAT'} "
                f"on floor {new_game_state.get('floor')}, score {screen.get('score')}")
        if not state.get("in_game") and tally.decisions:
            break
    else:
        tally.aborted = f"hit max_steps={max_steps}"
        say(f"{tally.aborted}; stopping")

    if floor_entry and tally.decisions:
        say(_floor_summary(floor_entry, state.get("game_state") or prev_game_state, floor_decisions, floor_usage))
    tally.end_state = state
    return state
