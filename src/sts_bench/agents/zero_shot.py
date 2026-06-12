"""Zero-shot stepwise agent: one decision point, one fresh conversation.

Each decision starts from the cursory state view; the model may call
observation tools (answered inline from the held state), then must commit to
exactly one action tool call. The only context carried across decisions is a
short trail of what happened recently. This is the stateless ablation
baseline; the default scaffold is the floor agent.
"""

from __future__ import annotations

from collections import deque

from ..state.schema import StateMessage
from ..state.serialize import cursory_view
from .base import ACTION_PROTOCOL, GOAL_AND_RULES, READING_THE_VIEW, Decision, ToolLoopAgent

TRAIL_CONTEXT = """\
A <recent_decisions> section, when present, lists what you did at the last few \
decision points (oldest first). Use it to avoid going in circles: if you already \
visited a screen and nothing has changed, pick a different action this time \
(e.g. proceed past a shop you just left)."""

SYSTEM_PROMPT = "\n\n".join((GOAL_AND_RULES, TRAIL_CONTEXT, READING_THE_VIEW, ACTION_PROTOCOL))

DEFAULT_HISTORY_SIZE = 8


class ZeroShotAgent(ToolLoopAgent):
    def __init__(self, *args, history_size: int = DEFAULT_HISTORY_SIZE, **kwargs):
        super().__init__(*args, **kwargs)
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
        return self._run_rounds(conversation, message, decision)
