"""Zero-shot stepwise agent: one decision point, one fresh conversation.

Each decision starts from the cursory state view; the model may call
observation tools (answered inline from the held state), then must commit to
exactly one action tool call. Invalid actions come back as the validator's
rejection text and the model retries within a bounded budget. When the budget
runs out the agent returns no action and the caller plays a safe fallback --
that forced action is part of the benchmark record, never hidden.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..actions.schema import Action
from ..actions.validate import validate
from ..providers.base import ModelProvider, ToolCall, Usage
from ..state.schema import StateMessage
from ..state.serialize import cursory_view
from ..tools.registry import ToolError, ToolRegistry

SYSTEM_PROMPT = """\
You are playing Slay the Spire as the {character}. Climb the spire: survive \
combats, choose your path on the map, and build your deck toward winning the run.

At each decision point you get a compact view of the game. Use the observation \
tools (get_deck, get_map, get_relics, get_potions, the pile tools) whenever you \
need details that are not in the view -- they are free and do not advance the game.

A <recent_decisions> section, when present, lists what you did at the last few \
decision points (oldest first). Use it to avoid going in circles: if you already \
visited a screen and nothing has changed, pick a different action this time \
(e.g. proceed past a shop you just left).

You may reason briefly in plain text before or alongside your tool calls; \
only a tool call acts on the game.

When you have decided, respond with exactly one action tool call \
(play_card, end_turn, choose, use_potion, discard_potion, proceed, return_back). \
All indices are 0-based exactly as shown in the listings. If an action is \
rejected, read the rejection reason and pick a legal alternative."""

NUDGE = (
    "When you are ready, commit with exactly one action tool call (observation "
    "tool calls first are fine if you still need information). Reasoning in "
    "plain text is welcome, but only a tool call acts on the game."
)

DEFAULT_MAX_ROUNDS = 10
DEFAULT_MAX_INVALID = 3
DEFAULT_HISTORY_SIZE = 8


@dataclass
class Decision:
    """What one decision point produced, including the cost of producing it."""

    action: Action | None  # None -> caller must fall back to a safe legal action
    forced_reason: str | None = None
    rounds: int = 0
    observation_calls: int = 0
    invalid_actions: int = 0
    usage: Usage = field(default_factory=Usage)
    transcript: list[dict] = field(default_factory=list)


class ZeroShotAgent:
    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        max_invalid: int = DEFAULT_MAX_INVALID,
        history_size: int = DEFAULT_HISTORY_SIZE,
    ):
        self._provider = provider
        self._registry = registry or ToolRegistry()
        self._tools = self._registry.openai_tools()
        self._max_rounds = max_rounds
        self._max_invalid = max_invalid
        self._recent: deque[str] = deque(maxlen=history_size)

    def record(self, line: str) -> None:
        """Note what actually happened at a decision point.

        The caller reports the executed action (the agent's own, or a forced
        fallback) after the game accepts it, so the trail shown to the model
        never claims something that didn't happen. Each decision still runs in
        a fresh conversation; this trail is its only cross-decision context.
        """
        self._recent.append(line)

    def decide(self, message: StateMessage) -> Decision:
        character = message.game_state.character if message.game_state else "adventurer"
        view = cursory_view(message)
        if self._recent:
            trail = "\n".join(self._recent)
            view += f"\n<recent_decisions>\n{trail}\n</recent_decisions>"
        conversation: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(character=character)},
            {"role": "user", "content": view},
        ]
        decision = Decision(action=None, transcript=conversation)

        for _ in range(self._max_rounds):
            decision.rounds += 1
            response = self._provider.complete(conversation, tools=self._tools)
            decision.usage += response.usage
            conversation.append(response.message)

            if not response.tool_calls:
                conversation.append({"role": "user", "content": NUDGE})
                continue

            for call in response.tool_calls:
                outcome = self._handle_call(call, message, decision)
                if isinstance(outcome, str):
                    conversation.append(_tool_result(call, outcome))
                else:
                    decision.action = outcome
                    return decision
            if decision.invalid_actions > self._max_invalid:
                decision.forced_reason = (
                    f"{decision.invalid_actions} invalid actions (budget {self._max_invalid})"
                )
                return decision

        decision.forced_reason = f"no valid action within {self._max_rounds} rounds"
        return decision

    def _handle_call(
        self, call: ToolCall, message: StateMessage, decision: Decision
    ) -> Action | str:
        """Resolve one tool call: a valid Action ends the decision, a string is fed back."""
        if call.arguments_error is not None:
            decision.invalid_actions += 1
            return call.arguments_error

        kind = self._registry.kind_of(call.name)
        if kind == "observation":
            decision.observation_calls += 1
            return self._registry.observe(call.name, message)

        try:
            action = self._registry.parse_action(call.name, call.arguments or {})
        except ToolError as exc:
            decision.invalid_actions += 1
            return str(exc)

        verdict = validate(action, message)
        if not verdict.ok:
            decision.invalid_actions += 1
            return f"action rejected: {verdict.reason}"
        return action


def _tool_result(call: ToolCall, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call.id, "content": content}
