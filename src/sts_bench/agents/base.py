"""Shared decision loop: rounds of provider calls until one action commits.

An agent scaffold owns its conversation -- what context each decision sees is
the scaffold's whole identity -- but the loop body is common: offer the tools,
answer observation calls inline from the held state, feed validator rejections
back as corrective tool results, and stop at the first valid action or when a
budget runs out. When the budget runs out the decision carries no action and
the caller plays a safe fallback -- that forced action is part of the
benchmark record, never hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..actions.schema import Action
from ..actions.validate import validate
from ..providers.base import ModelProvider, ToolCall, Usage
from ..state.schema import StateMessage
from ..tools.registry import ToolError, ToolRegistry

NUDGE = (
    "When you are ready, commit with exactly one action tool call (observation "
    "tool calls first are fine if you still need information). Reasoning in "
    "plain text is welcome, but only a tool call acts on the game."
)

# System-prompt sections shared by every scaffold. They carry what the game
# itself teaches a player -- the goal and the tutorial rules -- and how this
# harness presents the game; strategy stays out (advisor prompts are a
# separate, flagged scaffold), so the benchmark measures play, not coaching.

GOAL_AND_RULES = """\
You are playing Slay the Spire as the {character}. The spire has three acts, \
each a branching map of floors ending in a boss; defeat the act 3 boss to win \
the run. Your HP carries over between fights, and if it reaches 0 the run is \
over for good.

Combat rules: each of your turns you draw a fresh hand and your energy \
refills. At the end of your turn, unplayed cards go to the discard pile and \
unspent energy is lost. Block expires at the start of your turn. When the \
draw pile runs out, the discard pile reshuffles into it. Each enemy's intent \
shows what it will do on its next turn."""

READING_THE_VIEW = """\
At each decision point you get a compact view of the game. Card listings show \
the energy cost in parentheses after the name. Intent damage numbers are \
already adjusted for active effects; card text is the base printing -- your \
powers and debuffs apply on top. Use the observation tools (get_deck, \
get_map, get_relics, get_potions, the pile tools) whenever you need details \
that are not in the view -- they are free and do not advance the game."""

ACTION_PROTOCOL = """\
You may reason briefly in plain text before or alongside your tool calls; \
only a tool call acts on the game.

When you have decided, respond with exactly one action tool call \
(play_card, end_turn, choose, use_potion, discard_potion, proceed, return_back). \
All indices are 0-based exactly as shown in the listings. If an action is \
rejected, read the rejection reason and pick a legal alternative."""

DEFAULT_MAX_ROUNDS = 10
DEFAULT_MAX_INVALID = 3


@dataclass
class Decision:
    """What one decision point produced, including the cost of producing it."""

    action: Action | None  # None -> caller must fall back to a safe legal action
    action_call_id: str | None = None  # tool call the action arrived in
    forced_reason: str | None = None
    rounds: int = 0
    observation_calls: int = 0
    invalid_actions: int = 0
    usage: Usage = field(default_factory=Usage)
    # The conversation messages new to this decision (for a stateless agent,
    # the whole conversation). Loggers and stores must consume exactly this:
    # re-emitting a persistent conversation in full at every decision grows
    # quadratically.
    transcript: list[dict] = field(default_factory=list)


class ToolLoopAgent:
    """Base for agent scaffolds: subclasses build the conversation, this runs it."""

    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        max_invalid: int = DEFAULT_MAX_INVALID,
    ):
        self._provider = provider
        self._registry = registry or ToolRegistry()
        self._tools = self._registry.openai_tools()
        self._max_rounds = max_rounds
        self._max_invalid = max_invalid

    def _run_rounds(
        self, conversation: list[dict], message: StateMessage, decision: Decision
    ) -> Decision:
        """Drive the conversation until one action commits or a budget runs out."""
        for _ in range(self._max_rounds):
            decision.rounds += 1
            response = self._provider.complete(conversation, tools=self._tools)
            decision.usage += response.usage
            conversation.append(response.message)

            if not response.tool_calls:
                conversation.append({"role": "user", "content": NUDGE})
                continue

            for index, call in enumerate(response.tool_calls):
                outcome = self._handle_call(call, message, decision)
                if isinstance(outcome, str):
                    conversation.append(tool_result(call.id, outcome))
                    continue
                decision.action = outcome
                decision.action_call_id = call.id
                # Sibling calls after the committed action stay unexecuted;
                # close them so the conversation never holds an unanswered call.
                for extra in response.tool_calls[index + 1 :]:
                    conversation.append(
                        tool_result(
                            extra.id,
                            f"not executed: this decision point already committed {call.name}",
                        )
                    )
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


def tool_result(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}
