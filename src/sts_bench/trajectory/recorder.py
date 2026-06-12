"""Run recorder: turns the play loop's events into trajectory records.

The recorder owns the storage view of the run. It accumulates each floor's
conversation from decision transcript deltas -- for the floor agent the
deltas concatenate to exactly the conversation the provider last saw, which
is what makes the floor record a faithful packet -- and writes records in
the order things happen: decisions as they commit, a floor record (scorecard
and reward included) when the game moves to the next floor, the run record
at the end. Raw boundary game states go down with each floor so rewards stay
recomputable under future specs.
"""

from __future__ import annotations

import datetime
from collections import Counter
from typing import Any

from ..agents.base import Decision
from ..env.rewards import floor_reward
from ..providers.base import Usage
from .jsonl import TrajectoryStore
from .schema import (
    DecisionRecord,
    FloorRecord,
    FloorScorecard,
    RunOutcome,
    RunRecord,
    RunTotals,
    StateSummary,
    TokenUsage,
)


def screen_label(game_state: dict[str, Any]) -> str:
    """Screen name for narration and records. The wire says NONE for combat
    with no overlay on top; call that what it is."""
    screen = game_state.get("screen_type")
    if screen == "NONE":
        return "COMBAT" if game_state.get("combat_state") else "NONE"
    return screen or "?"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _summarize(state: dict[str, Any]) -> StateSummary:
    return StateSummary(
        floor=state.get("floor"),
        hp=state.get("current_hp"),
        max_hp=state.get("max_hp"),
        gold=state.get("gold"),
        screen=screen_label(state),
    )


def _gained(entry: list[dict] | None, exit_: list[dict] | None) -> list[str]:
    """Names present at exit beyond what entry had, as a multiset diff --
    a second Strike+ counts, a reshuffled deck does not."""
    held = Counter(item.get("name") for item in entry or [])
    gained = []
    for item in exit_ or []:
        name = item.get("name")
        if name == "Potion Slot":  # empty belt slots travel as placeholder potions
            continue
        if held[name] > 0:
            held[name] -= 1
        else:
            gained.append(name)
    return gained


class RunRecorder:
    def __init__(
        self,
        store: TrajectoryStore,
        *,
        run_id: str,
        seed: str | None,
        character: str,
        ascension: int,
        model: str,
        provider_base_url: str,
        api: str,
        reasoning_effort: str | None,
        agent: str,
        prompt_hash: str | None = None,
        tool_schema_hash: str | None = None,
    ):
        self._store = store
        self._run = RunRecord(
            run_id=run_id,
            started_at=_now(),
            seed=seed,
            character=character,
            ascension=ascension,
            model=model,
            provider_base_url=provider_base_url,
            api=api,
            reasoning_effort=reasoning_effort,
            agent=agent,
            prompt_hash=prompt_hash,
            tool_schema_hash=tool_schema_hash,
        )
        self._entry_state: dict[str, Any] | None = None
        self._last_state: dict[str, Any] | None = None
        self._messages: list[dict[str, Any]] = []
        self._combat_turns: int | None = None
        self._decisions = 0
        self._forced = 0
        self._unparsed = 0
        self._usage = Usage()
        self._finished = False

    def observe(self, game_state: dict[str, Any]) -> None:
        """Every state the loop sees comes through here, before its decision."""
        if not game_state:
            return
        if self._entry_state is None:
            self._entry_state = game_state
        elif game_state.get("floor") != self._entry_state.get("floor"):
            # The new floor's first state is the old floor's exit boundary --
            # it is the state that proves the floor advanced, which the
            # reward spec reads straight off the stored pair.
            self._flush_floor(game_state)
            self._entry_state = game_state
        self._last_state = game_state

        combat = game_state.get("combat_state") or {}
        if combat.get("turn"):
            self._combat_turns = max(self._combat_turns or 0, combat["turn"])
        if game_state.get("screen_type") == "GAME_OVER":
            screen = game_state.get("screen_state") or {}
            self._run.outcome = RunOutcome(
                victory=bool(screen.get("victory")),
                floor_reached=game_state.get("floor"),
                score=screen.get("score"),
            )

    def decision(
        self,
        decision: Decision,
        *,
        screen: str | None,
        action: str | None,
        command: str | None,
        latency_ms: int | None = None,
    ) -> None:
        self._decisions += 1
        if decision.forced_reason is not None:
            self._forced += 1
        self._usage += decision.usage
        start = len(self._messages)
        self._messages.extend(decision.transcript)
        self._store.append(
            DecisionRecord(
                run_id=self._run.run_id,
                floor=(self._entry_state or {}).get("floor"),
                decision_index=self._decisions,
                message_start=start,
                message_end=len(self._messages),
                screen=screen,
                action=action,
                command=command,
                forced_reason=decision.forced_reason,
                rounds=decision.rounds,
                observation_calls=decision.observation_calls,
                invalid_actions=decision.invalid_actions,
                latency_ms=latency_ms,
                usage=TokenUsage.from_usage(decision.usage),
            )
        )

    def unparsed_state(self) -> None:
        self._unparsed += 1

    def finish(self) -> RunRecord:
        """Close the floor in progress and write the run record. Safe to call
        from a finally block: runs once, later calls return the same record."""
        if self._finished:
            return self._run
        self._finished = True
        if self._entry_state is not None:
            self._flush_floor(self._last_state or self._entry_state)
        self._run.finished_at = _now()
        if self._run.outcome.floor_reached is None and self._last_state is not None:
            self._run.outcome.floor_reached = self._last_state.get("floor")
        self._run.totals = RunTotals(
            decisions=self._decisions,
            forced=self._forced,
            unparsed_states=self._unparsed,
            usage=TokenUsage.from_usage(self._usage),
        )
        self._store.append(self._run)
        return self._run

    def _flush_floor(self, exit_state: dict[str, Any]) -> None:
        entry = self._entry_state
        assert entry is not None
        scorecard = FloorScorecard(
            hp_delta=(exit_state.get("current_hp") or 0) - (entry.get("current_hp") or 0),
            gold_delta=(exit_state.get("gold") or 0) - (entry.get("gold") or 0),
            cards_gained=_gained(entry.get("deck"), exit_state.get("deck")),
            relics_gained=_gained(entry.get("relics"), exit_state.get("relics")),
            potions_gained=_gained(entry.get("potions"), exit_state.get("potions")),
            combat_turns=self._combat_turns,
        )
        self._store.append(
            FloorRecord(
                run_id=self._run.run_id,
                floor=entry.get("floor") or 0,
                floor_type=entry.get("room_type"),
                conversation=self._messages,
                entry=_summarize(entry),
                exit=_summarize(exit_state),
                entry_state=entry,
                exit_state=exit_state,
                scorecard=scorecard,
                reward=floor_reward(entry, exit_state),
            )
        )
        self._messages = []
        self._combat_turns = None
