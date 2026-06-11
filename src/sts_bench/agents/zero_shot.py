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
from .base import Decision, ToolLoopAgent

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
