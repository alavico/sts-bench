"""Floor agent: one conversation per floor. The default scaffold.

The unit of context is the floor, not the decision. What matters to a decision
is what happened earlier *this* floor -- previous combat turns, earlier
branches of a multi-part event, the shop already visited -- not the tail of
the previous fight. So every decision appends to the same conversation: first
a tool result closing the previous action call with what actually happened
(executed, rejected with the reason, or a fallback played by the harness),
then the fresh state digest, then the model's rounds.

On floor change the conversation resets to the system prompt plus the new
digest, with one carried-over line summarizing the floor just finished --
cheap insurance against revisit loops at the boundary. The reset makes the
floor's final request + final response a complete, self-contained record of
the floor (the packet property the trajectory store leans on), and within a
floor each request extends the previous one, the shape provider prompt caches
are built for.
"""

from __future__ import annotations

from typing import Any

from ..state.schema import StateMessage
from ..state.serialize import combat_briefing, cursory_view, power_definitions
from .base import ACTION_PROTOCOL, GOAL_AND_RULES, READING_THE_VIEW, Decision, ToolLoopAgent, tool_result

FLOOR_CONTEXT = """\
This conversation covers the current floor: your earlier decisions here stay \
in context, and each action you take receives a result reporting what actually \
happened -- it was executed, or it was rejected (with the reason), or the \
harness played a fallback instead. A new floor starts a fresh conversation, \
opened with a one-line summary of the floor before. Use the history: do not \
repeat an action that already failed or revisit a screen that has not changed."""

SYSTEM_PROMPT = "\n\n".join((GOAL_AND_RULES, FLOOR_CONTEXT, READING_THE_VIEW, ACTION_PROTOCOL))


class FloorAgent(ToolLoopAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._conversation: list[dict] = []
        self._floor: int | None = None
        self._pending_call_id: str | None = None
        self._outcome: str | None = None
        self._floor_outcomes: list[str] = []
        self._combat_briefed = False
        self._powers_defined: frozenset[str] = frozenset()

    def record(self, line: str) -> None:
        """Note what actually happened at a decision point.

        The caller reports the executed action (the agent's own, or a forced
        fallback) after the game accepts it. The next decision delivers it to
        the model as the result of its pending action call, so the model
        learns its action's fate in-conversation -- never something that
        didn't happen.
        """
        self._outcome = line
        self._floor_outcomes.append(line)

    def resume(
        self,
        *,
        floor: int | None,
        conversation: list[dict[str, Any]],
        pending_call_id: str | None,
        outcome: str | None,
        floor_outcomes: list[str],
        in_combat: bool,
    ) -> None:
        """Restore the cross-decision state needed to continue a floor.

        A resumed run has already replayed the wire commands into the live
        game. The agent still needs its provider conversation and the result
        line for the last executed action so the next model request closes the
        pending action tool call exactly like uninterrupted play would.
        """
        self._conversation = [dict(m) for m in conversation]
        self._floor = floor
        self._pending_call_id = pending_call_id
        self._outcome = outcome
        self._floor_outcomes = list(floor_outcomes)
        self._combat_briefed = in_combat and any(
            "<deck_reference>" in str(m.get("content") or "")
            for m in self._conversation
            if m.get("role") == "user"
        )
        self._powers_defined = frozenset()

    def decide(self, message: StateMessage) -> Decision:
        state = message.game_state
        floor = state.floor if state else None
        prefix: list[str] = []

        if not self._conversation or floor != self._floor:
            character = state.character if state else "adventurer"
            carried = self._carried_line()
            self._conversation = [
                {"role": "system", "content": SYSTEM_PROMPT.format(character=character)}
            ]
            self._floor = floor
            self._floor_outcomes = []
            # any pending call belongs to the abandoned conversation; its
            # reported fate already rides the carried line
            self._pending_call_id = None
            self._outcome = None
            self._combat_briefed = False
            delta_start = 0
            if carried:
                prefix.append(carried)
        else:
            delta_start = len(self._conversation)
            outcome = self._outcome or "(outcome not reported)"
            if self._pending_call_id is not None:
                self._conversation.append(tool_result(self._pending_call_id, outcome))
            elif self._outcome is not None:
                # no open call to answer (the decision was forced), so the
                # fate arrives as a note on the fresh digest instead
                prefix.append(f"<note>{outcome}</note>")
            self._pending_call_id = None
            self._outcome = None

        # Brief each combat once with the relic bar and the deck's printed
        # card text; the conversation keeps it in view for the whole fight.
        # Powers explain themselves the first time they appear in the fight
        # (a player hovers the new icon) -- monster-applied debuffs land
        # mid-combat, after any briefing could have covered them.
        if state is not None and state.combat_state is not None:
            if not self._combat_briefed:
                prefix.append(combat_briefing(state))
                self._combat_briefed = True
                self._powers_defined = frozenset()
            definitions, self._powers_defined = power_definitions(
                state.combat_state, self._powers_defined
            )
            if definitions:
                prefix.append(definitions)
        else:
            self._combat_briefed = False

        view = "\n".join((*prefix, cursory_view(message)))
        self._conversation.append({"role": "user", "content": view})
        decision = Decision(action=None)
        self._run_rounds(self._conversation, message, decision)
        decision.transcript = self._conversation[delta_start:]
        if decision.action is not None:
            self._pending_call_id = decision.action_call_id
        return decision

    def _carried_line(self) -> str | None:
        if not self._floor_outcomes:
            return None
        return (
            f"<previous_floor>{len(self._floor_outcomes)} decisions; "
            f"last: {self._floor_outcomes[-1]}</previous_floor>"
        )
