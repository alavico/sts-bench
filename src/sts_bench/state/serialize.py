"""Cursory state view: the compact digest the model sees at every decision point.

Design (docs/spec.md): plain-text lines inside XML section delimiters -- tags
mark the regions, prose stays terse. Deliberately *not* everything: deck
contents, full map, draw/discard piles and card/relic text live behind
observation tools. What is always here: who/where, the screen, the combat
essentials (hand, energy, enemy intents), the choice list, and what commands
are legal.

This rendering is a research artifact; its token count is a tracked metric and
the renderer stays swappable for format ablations.
"""

from __future__ import annotations

from .schema import (
    Card,
    CardRewardScreen,
    CombatRewardScreen,
    CombatState,
    EventScreen,
    GameState,
    GridScreen,
    MapScreen,
    Monster,
    StateMessage,
)

EMPTY_POTION_ID = "Potion Slot"

# Harness-level commands the model never sees; its action surface is the typed
# tools, and these would only invite junk like raw clicks.
HIDDEN_COMMANDS = frozenset({"key", "click", "wait", "state"})


def cursory_view(message: StateMessage) -> str:
    sections: list[str] = []
    state = message.game_state
    if not message.in_game or state is None:
        sections.append("<run>out of game (main menu)</run>")
    else:
        sections.append(f"<run>{_run_line(state)}</run>")
        screen = _screen_section(state)
        if screen:
            sections.append(screen)
        if state.combat_state is not None:
            sections.append(f"<combat>\n{_combat_lines(state.combat_state)}\n</combat>")
        if state.choice_list:
            choices = "\n".join(f"[{i}] {choice}" for i, choice in enumerate(state.choice_list))
            sections.append(f"<choices>\n{choices}\n</choices>")
    visible = [c for c in message.available_commands if c not in HIDDEN_COMMANDS]
    sections.append(f"<commands>{', '.join(visible)}</commands>")
    return "\n".join(sections)


def _run_line(state: GameState) -> str:
    potions = [p.name for p in state.potions if p.id != EMPTY_POTION_ID]
    parts = [
        f"{state.character} (ascension {state.ascension_level})",
        f"act {state.act} floor {state.floor}",
        f"HP {state.current_hp}/{state.max_hp}",
        f"gold {state.gold}",
        f"deck {len(state.deck)} cards",
        f"relics {len(state.relics)}",
        f"potions: {', '.join(potions) if potions else 'none'}",
    ]
    return " | ".join(parts)


def _screen_section(state: GameState) -> str | None:
    screen = state.screen
    match screen:
        case EventScreen():
            lines = [f"event: {screen.event_name}", screen.body_text.strip()]
            for option in screen.options:
                idx = "x" if option.choice_index is None else option.choice_index
                disabled = " (disabled)" if option.disabled else ""
                lines.append(f"[{idx}] {option.text}{disabled}")
            return f"<screen type=\"EVENT\">\n" + "\n".join(lines) + "\n</screen>"
        case MapScreen():
            lines = []
            if screen.current_node and screen.current_node.symbol:
                lines.append(f"at node {_node(screen.current_node)}")
            if screen.next_nodes:
                lines.append("next: " + ", ".join(_node(n) for n in screen.next_nodes))
            if screen.boss_available:
                lines.append("boss fight available")
            lines.append("(symbols: M monster, E elite, $ shop, R rest, T chest, ? event, B boss; full layout via get_map)")
            return "<screen type=\"MAP\">\n" + "\n".join(lines) + "\n</screen>"
        case CardRewardScreen():
            lines = [_card(card, i) for i, card in enumerate(screen.cards)]
            extras = []
            if screen.skip_available:
                extras.append("skipping is allowed")
            if screen.bowl_available:
                extras.append("Singing Bowl: +2 max HP instead")
            if extras:
                lines.append("; ".join(extras))
            return "<screen type=\"CARD_REWARD\">\n" + "\n".join(lines) + "\n</screen>"
        case CombatRewardScreen():
            lines = []
            for reward in screen.rewards:
                if reward.gold is not None:
                    lines.append(f"{reward.reward_type}: {reward.gold} gold")
                elif reward.potion is not None:
                    lines.append(f"{reward.reward_type}: {reward.potion.name}")
                elif reward.relic is not None:
                    lines.append(f"{reward.reward_type}: {reward.relic.name}")
                else:
                    lines.append(reward.reward_type)
            return "<screen type=\"COMBAT_REWARD\">\n" + "\n".join(lines) + "\n</screen>"
        case GridScreen():
            purpose = (
                "purge" if screen.for_purge else "transform" if screen.for_transform else "upgrade" if screen.for_upgrade else "select"
            )
            count = "any number of cards" if screen.any_number else f"{screen.num_cards} card(s)"
            lines = [f"pick {count} to {purpose}"]
            lines += [_card(card, i) for i, card in enumerate(screen.cards)]
            if screen.selected_cards:
                lines.append("selected: " + ", ".join(c.name for c in screen.selected_cards))
            if screen.confirm_up:
                lines.append("confirm to finish")
            return "<screen type=\"GRID\">\n" + "\n".join(lines) + "\n</screen>"
    return None  # NONE screen: combat section carries the content


def _combat_lines(combat: CombatState) -> str:
    player = combat.player
    lines = [f"turn {combat.turn} | energy {player.energy} | block {player.block}"]
    if player.powers:
        lines.append("you: " + ", ".join(_power(p) for p in player.powers))
    orbs = [o for o in player.orbs if o.id is not None]
    if orbs:
        lines.append("orbs: " + ", ".join(f"{o.name}({o.passive_amount}/{o.evoke_amount})" for o in orbs)
                     + f" [{len(player.orbs)} slots]")
    for i, monster in enumerate(combat.monsters):
        if monster.is_gone:
            continue
        lines.append(_monster(monster, i))
    for i, card in enumerate(combat.hand):
        lines.append("hand" + _card(card, i, show_target=True))
    lines.append(
        f"piles: draw {len(combat.draw_pile)}, discard {len(combat.discard_pile)}, "
        f"exhaust {len(combat.exhaust_pile)} (contents via tools)"
    )
    return "\n".join(lines)


def _monster(monster: Monster, index: int) -> str:
    intent = monster.intent.value
    if monster.move_adjusted_damage >= 0:
        intent += f" {monster.move_adjusted_damage}x{monster.move_hits}"
    powers = ("; " + ", ".join(_power(p) for p in monster.powers)) if monster.powers else ""
    block = f" block {monster.block}" if monster.block else ""
    return (
        f"enemy[{index}] {monster.name} {monster.current_hp}/{monster.max_hp}{block} | intent {intent}{powers}"
    )


def _card(card: Card, index: int, show_target: bool = False) -> str:
    upgraded = "+" * card.upgrades
    tags = ""
    if show_target and card.has_target:
        tags += " [needs target]"
    if card.is_playable is False:
        tags += " [unplayable]"
    return f"[{index}] {card.name}{upgraded} ({card.cost}){tags}"


def _power(power) -> str:
    return f"{power.name} {power.amount}"


def _node(node) -> str:
    return f"{node.symbol or '?'} (x={node.x})"
