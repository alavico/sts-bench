"""Observation tools: on-demand views answered from the state already in hand.

Each handler takes the current StateMessage and returns plain text -- no game
round-trips, no hidden information. Everything here is legally visible to a
human player: deck and pile *contents* are known but draw order is not, so
card listings are grouped and sorted, never in pile order.
"""

from __future__ import annotations

from collections import Counter

from ..state.schema import Card, MapNode, StateMessage
from ..state.serialize import HIDDEN_COMMANDS

NOT_IN_RUN = "not in a run right now"
MAP_LEGEND = "M monster, E elite, $ shop, R rest, T chest, ? event, B boss"


def get_deck(message: StateMessage) -> str:
    state = message.game_state
    if state is None:
        return NOT_IN_RUN
    return f"deck ({len(state.deck)} cards):\n" + _card_listing(state.deck)


def get_draw_pile(message: StateMessage) -> str:
    return _pile(message, "draw_pile", "draw pile (order unknown, as in game)")


def get_discard_pile(message: StateMessage) -> str:
    return _pile(message, "discard_pile", "discard pile")


def get_exhaust_pile(message: StateMessage) -> str:
    return _pile(message, "exhaust_pile", "exhaust pile")


def get_relics(message: StateMessage) -> str:
    state = message.game_state
    if state is None:
        return NOT_IN_RUN
    lines = []
    for relic in state.relics:
        counter = f" (counter {relic.counter})" if relic.counter >= 0 else ""
        lines.append(f"{relic.name}{counter}")
    return f"relics ({len(lines)}):\n" + "\n".join(lines) if lines else "no relics"


def get_potions(message: StateMessage) -> str:
    state = message.game_state
    if state is None:
        return NOT_IN_RUN
    lines = []
    for i, potion in enumerate(state.potions):
        if potion.id == "Potion Slot":
            lines.append(f"[{i}] (empty slot)")
            continue
        notes = []
        notes.append("usable now" if potion.can_use else "not usable now")
        if potion.requires_target:
            notes.append("needs a target")
        if potion.can_discard:
            notes.append("discardable")
        lines.append(f"[{i}] {potion.name} -- {', '.join(notes)}")
    return "potions:\n" + "\n".join(lines) if lines else "no potion slots"


def get_legal_actions(message: StateMessage) -> str:
    """Map the game's available commands to the action tools they unlock."""
    commands = set(message.available_commands) - HIDDEN_COMMANDS
    state = message.game_state
    lines = []
    if "play" in commands:
        lines.append("play_card -- combat, your turn")
    if "end" in commands:
        lines.append("end_turn")
    if "choose" in commands:
        n = len(state.choice_list) if state else 0
        lines.append(f"choose -- choice_index 0 to {n - 1}" if n else "choose")
    if "potion" in commands:
        lines.append("use_potion / discard_potion")
    if commands.intersection(("proceed", "confirm")):
        lines.append("proceed -- advance past this screen")
    if commands.intersection(("return", "skip", "cancel", "leave")):
        lines.append("return_back -- back out / skip this screen")
    return "legal action tools now:\n" + "\n".join(lines) if lines else "no actions available (waiting)"


def get_map(message: StateMessage) -> str:
    """Full act layout as floor-by-floor adjacency, bottom to top.

    `y` is the floor within the act (0 at the bottom). Each node lists the
    nodes it connects to on the floor above; you can only move along those
    edges. Top-floor nodes all lead to the act boss.
    """
    state = message.game_state
    if state is None:
        return NOT_IN_RUN
    if not state.map:
        return "no map available on this screen"

    by_pos = {(node.x, node.y): node for node in state.map}
    floors: dict[int, list[MapNode]] = {}
    for node in state.map:
        floors.setdefault(node.y, []).append(node)

    lines = [f"act {state.act} map, boss: {state.act_boss} (symbols: {MAP_LEGEND})"]
    for y in sorted(floors):
        entries = []
        for node in sorted(floors[y], key=lambda n: n.x):
            children = ", ".join(
                _node_label(by_pos.get((ref.x, ref.y))) for ref in node.children
            )
            entries.append(f"{node.symbol}(x={node.x}) -> {children or 'BOSS'}")
        lines.append(f"floor y={y}: " + "; ".join(entries))
    return "\n".join(lines)


def _node_label(node: MapNode | None) -> str:
    # An edge pointing past the listed floors is the act boss (e.g. {x:3, y:16}).
    return f"{node.symbol}(x={node.x})" if node else "BOSS"


def _pile(message: StateMessage, attr: str, title: str) -> str:
    state = message.game_state
    if state is None:
        return NOT_IN_RUN
    if state.combat_state is None:
        return "not in combat"
    cards = getattr(state.combat_state, attr)
    if not cards:
        return f"{title}: empty"
    return f"{title} ({len(cards)} cards):\n" + _card_listing(cards)


def _card_listing(cards: list[Card]) -> str:
    counts = Counter(_card_line(card) for card in cards)
    return "\n".join(f"{n}x {line}" for line, n in sorted(counts.items()))


def _card_line(card: Card) -> str:
    upgraded = "+" * card.upgrades
    return f"{card.name}{upgraded} (cost {card.cost}, {card.type.value})"
