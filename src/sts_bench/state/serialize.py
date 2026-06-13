"""Cursory state view: the compact digest the model sees at every decision point.

Format: plain-text lines inside XML section delimiters -- tags mark the
regions, prose stays terse. Deliberately *not* everything: deck
contents, full map, draw/discard piles and card/relic text live behind
observation tools. What is always here: who/where, the screen, the combat
essentials (hand, energy, enemy intents), the choice list, and what commands
are legal.

This rendering is a research artifact; its token count is a tracked metric and
the renderer stays swappable for format ablations.
"""

from __future__ import annotations

from ..tools.card_db import card_text, upgraded_card_text
from ..tools.keyword_db import keyword_lines
from ..tools.potion_db import potion_text
from ..tools.power_db import amount_is_sentinel, power_text
from ..tools.relic_db import relic_text
from .schema import (
    EMPTY_POTION_ID,
    HIDDEN_COMMANDS,
    BossRewardScreen,
    Card,
    CardRewardScreen,
    ChestScreen,
    CombatRewardScreen,
    CombatState,
    EventScreen,
    GameOverScreen,
    GameState,
    GridScreen,
    HandSelectScreen,
    MapScreen,
    Monster,
    RestScreen,
    ShopScreen,
    StateMessage,
)

# The model acts through tools, so the legal-commands line speaks tool names,
# keeping the game's own button word visible where it differs (the screen
# says "leave"/"confirm"; the tools say return_back/proceed).
TOOL_COMMAND_NAMES = {
    "play": "play_card",
    "end": "end_turn",
    "potion": "use_potion, discard_potion",
    "confirm": "proceed (confirm)",
    "return": "return_back",
    "skip": "return_back (skip)",
    "cancel": "return_back (cancel)",
    "leave": "return_back (leave)",
}


# What each campfire button says on the real screen (relic-granted options
# included). Unknown options render as the wire word, loudly bare.
REST_OPTION_TEXT = {
    "rest": "heal 30% of max HP",
    "smith": "upgrade a card in your deck",
    "recall": "obtain the Ruby Key (one of the 3 keys that unlock the final act)",
    "lift": "gain 1 permanent Strength (Girya)",
    "toke": "remove a card from your deck (Peace Pipe)",
    "dig": "obtain a random relic (Shovel)",
}


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
            deck_names = frozenset(card.name for card in state.deck)
            sections.append(f"<combat>\n{_combat_lines(state.combat_state, deck_names)}\n</combat>")
        # The loot screen renders its own indexed lines; a second list of the
        # same items in keyword form only invites misreads.
        if state.choice_list and not isinstance(state.screen, CombatRewardScreen):
            lines = [f"[{i}] {choice}" for i, choice in enumerate(state.choice_list)]
            # The Skip button is part of the card-pick menu on the real
            # screen; a choices list without it reads as "you must take one",
            # and skipping is often the right play.
            if isinstance(state.screen, CardRewardScreen) and state.screen.skip_available:
                lines.append("or skip -- take no card (return_back)")
            sections.append("<choices>\n" + "\n".join(lines) + "\n</choices>")
    visible = [c for c in message.available_commands if c not in HIDDEN_COMMANDS]
    # Observation tools ride along explicitly: a model inferring "what may I
    # call here" from this line alone otherwise concludes lookups are off
    # the table (observed live: a shop decision declined get_deck for it).
    sections.append(
        f"<commands>{', '.join(TOOL_COMMAND_NAMES.get(c, c) for c in visible)}"
        " + observation tools (always available)</commands>"
    )
    return "\n".join(sections)


def combat_briefing(state: GameState) -> str:
    """What a player sees at the top of every fight without clicking around:
    the relic bar, the potion belt, and the deck's printed card text (hover
    text included throughout). The floor agent injects this once per combat;
    the conversation keeps it in view for the rest of the fight."""
    parts = [relic_bar(state)]
    belt = potion_belt(state)
    if belt:
        parts.append(belt)
    parts.append(deck_reference(state))
    # The tooltip boxes a player sees beside those texts: define each game
    # term the briefing actually uses, once.
    glossary = keyword_lines(
        [card_text(c) for c in state.deck]
        + [relic_text(r) for r in state.relics]
        + [potion_text(p) for p in state.potions]
    )
    if glossary:
        parts.append("<keywords>\n" + "\n".join(glossary) + "\n</keywords>")
    return "\n".join(parts)


def power_definitions(
    combat: CombatState, seen: frozenset[str]
) -> tuple[str | None, frozenset[str]]:
    """Tooltips for powers newly in play this decision; the hover text a
    player reads the first time an icon appears.

    `seen` carries the power ids already defined this combat (the floor
    agent's conversation keeps earlier definitions in view), so each power
    explains itself exactly once per fight no matter how many times it
    stacks or reappears. Returns the section (None when nothing is new) and
    the updated seen-set.
    """
    creatures = [combat.player, *[m for m in combat.monsters if not m.is_gone]]
    lines = []
    defined: set[str] = set()
    now_seen = set(seen)
    for creature in creatures:
        for power in creature.powers:
            if power.id in now_seen:
                continue
            now_seen.add(power.id)
            text = power_text(power)
            if text:
                lines.append(f"{power.name}: {text}")
                defined.add(power.name.lower())
    if not lines:
        return None, frozenset(now_seen)
    header = "powers in play -- X is the power's current amount, shown beside its name:"
    body = "\n".join(lines)
    # Terms inside the definitions get their tooltip too (Hex's "Dazed"), but
    # a keyword that *is* one of the powers just defined would only repeat it.
    glossary = [
        line for line in keyword_lines(lines) if line.split(":")[0].lower() not in defined
    ]
    if glossary:
        body += "\n" + "\n".join(glossary)
    return f"<power_definitions>\n{header}\n{body}\n</power_definitions>", frozenset(now_seen)


def potion_belt(state: GameState) -> str | None:
    held = [p for p in state.potions if p.id != EMPTY_POTION_ID]
    if not held:
        return None
    lines = [f"your potions ({len(held)}/{len(state.potions)}):"]
    for potion in held:
        text = potion_text(potion)
        lines.append(f"{potion.name}{f': {text}' if text else ''}")
    return "<potion_belt>\n" + "\n".join(lines) + "\n</potion_belt>"


def relic_bar(state: GameState) -> str:
    lines = ["your relics:"]
    for relic in state.relics:
        counter = f" (counter {relic.counter})" if relic.counter >= 0 else ""
        text = relic_text(relic)
        lines.append(f"{relic.name}{counter}{f': {text}' if text else ''}")
    return "<relic_bar>\n" + "\n".join(lines) + "\n</relic_bar>"


def deck_reference(state: GameState) -> str:
    """Combat briefing: the deck's printed card text, grouped and counted.

    A player reads this off the card faces; the model gets it once per combat
    (the floor agent injects it at combat start and the conversation keeps it
    in view), not in every digest. Buff-adjusted numbers are not computed --
    the combat section carries the buffs.
    """
    counts: dict[str, int] = {}
    first: dict[str, Card] = {}
    for card in state.deck:
        counts[card.name] = counts.get(card.name, 0) + 1
        first.setdefault(card.name, card)
    lines = ["your deck -- each line: name (energy cost): printed text"]
    for name, card in first.items():
        copies = f" x{counts[name]}" if counts[name] > 1 else ""
        text = card_text(card)
        lines.append(f"{name}{copies} ({card.cost}){f': {text}' if text else ''}")
    return "<deck_reference>\n" + "\n".join(lines) + "\n</deck_reference>"


def _run_line(state: GameState) -> str:
    # In combat the belt greys potions that can't be used right now (Fairy in
    # a Bottle, Smoke Bomb against a boss); out of combat nothing is usable,
    # so the flag carries no information and stays untagged.
    in_combat = state.combat_state is not None
    held = [
        p.name + (" [not usable now]" if in_combat and not p.can_use else "")
        for p in state.potions
        if p.id != EMPTY_POTION_ID
    ]
    slots = len(state.potions)
    full = ", full" if held and len(held) == slots else ""
    parts = [
        f"{state.character} (ascension {state.ascension_level})",
        f"act {state.act} floor {state.floor}",
        f"HP {state.current_hp}/{state.max_hp}",
        f"gold {state.gold}",
        f"deck {len(state.deck)} cards",
        f"relics {len(state.relics)}",
        f"potions ({len(held)}/{slots}{full}): {', '.join(held) if held else 'none'}",
    ]
    if state.keys is not None:
        # The key icons sit on the real HUD whenever act 4 is in play; all
        # three are needed to enter it and face the heart.
        got = [name for name in ("ruby", "emerald", "sapphire") if getattr(state.keys, name)]
        parts.append(f"keys {len(got)}/3 ({', '.join(got) if got else 'none'}; all 3 unlock the final act)")
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
            # The boss icon sits at the top of the real map; routing and
            # drafting are planned against it.
            lines.append(f"act boss: {state.act_boss}")
            lines.append("(symbols: M monster, E elite, $ shop, R rest, T chest, ? event, B boss; full layout via get_map)")
            return "<screen type=\"MAP\">\n" + "\n".join(lines) + "\n</screen>"
        case CardRewardScreen():
            # A player reads the full card before picking; so does the model,
            # tooltip definitions included.
            lines = [_card_with_text(card, i) for i, card in enumerate(screen.cards)]
            lines += keyword_lines(card_text(card) for card in screen.cards)
            if screen.bowl_available:
                lines.append("Singing Bowl: +2 max HP instead")
            return "<screen type=\"CARD_REWARD\">\n" + "\n".join(lines) + "\n</screen>"
        case CombatRewardScreen():
            # Loot is not a pick-one menu: every item is claimable, in any
            # order. The genuine decisions are the card (opens a skippable
            # pick) and a potion when the belt is full.
            if not screen.rewards:
                body = "all loot claimed -- proceed to move on"
            else:
                lines = ["loot -- take each in any order (choose), proceed when done:"]
                for i, reward in enumerate(screen.rewards):
                    lines.append(f"[{i}] {_reward(reward, state)}")
                body = "\n".join(lines)
            return f"<screen type=\"COMBAT_REWARD\">\n{body}\n</screen>"
        case GridScreen():
            purpose = (
                "purge" if screen.for_purge else "transform" if screen.for_transform else "upgrade" if screen.for_upgrade else "select"
            )
            count = "any number of cards" if screen.any_number else f"{screen.num_cards} card(s)"
            lines = [f"pick {count} to {purpose}"]
            # These picks happen outside combat, where no deck briefing is in
            # view -- print the text, and for upgrades the preview a player
            # gets by hovering.
            for i, card in enumerate(screen.cards):
                line = _card_with_text(card, i)
                if screen.for_upgrade:
                    preview = upgraded_card_text(card)
                    if preview:
                        line += f" | upgraded: {preview}"
                lines.append(line)
            if screen.selected_cards:
                lines.append("selected: " + ", ".join(c.name for c in screen.selected_cards))
            if screen.confirm_up:
                lines.append("confirm to finish")
            return "<screen type=\"GRID\">\n" + "\n".join(lines) + "\n</screen>"
        case RestScreen():
            if screen.has_rested:
                lines = ["already rested -- proceed to move on"]
            else:
                # Each campfire button explains itself in the real UI; bare
                # option words would make "recall" a vocabulary test.
                lines = ["options:"] + [
                    f"{option} -- {REST_OPTION_TEXT[option]}" if option in REST_OPTION_TEXT else option
                    for option in screen.rest_options
                ]
            return "<screen type=\"REST\">\n" + "\n".join(lines) + "\n</screen>"
        case ChestScreen():
            status = "already open" if screen.chest_open else "unopened"
            leave = "" if screen.chest_open else " -- open it (choose) or leave it shut (proceed)"
            return f"<screen type=\"CHEST\">\n{screen.chest_type} ({status}){leave}\n</screen>"
        case ShopScreen():
            # No bracketed indices here: buying goes through the <choices>
            # list, whose numbering (purge first, then wares) does not match
            # a per-category count.
            lines = []
            lines += [f"card: {_card_with_text(c)} -- {c.price} gold" for c in screen.cards]
            lines += [f"relic: {_with_text(r.name, relic_text(r))} -- {r.price} gold" for r in screen.relics]
            lines += [f"potion: {_with_text(p.name, potion_text(p))} -- {p.price} gold" for p in screen.potions]
            if screen.purge_available:
                lines.append(f"card removal -- {screen.purge_cost} gold")
            return "<screen type=\"SHOP\">\n" + "\n".join(lines) + "\n</screen>"
        case BossRewardScreen():
            lines = [
                f"[{i}] {_with_text(relic.name, relic_text(relic))}"
                for i, relic in enumerate(screen.relics)
            ]
            return "<screen type=\"BOSS_REWARD\">\npick one relic:\n" + "\n".join(lines) + "\n</screen>"
        case HandSelectScreen():
            zero = ", picking none is allowed" if screen.can_pick_zero else ""
            lines = [f"select up to {screen.max_cards} card(s) from hand{zero}"]
            if len(screen.selected) >= screen.max_cards:
                # Nothing left to choose; indexed lines would only invite a
                # dead `choose` call.
                lines.append("selected: " + ", ".join(c.name for c in screen.selected))
                lines.append(
                    f"selection complete ({len(screen.selected)}/{screen.max_cards}) "
                    "-- proceed to confirm"
                )
                if screen.hand:
                    lines.append("(unselected: " + ", ".join(c.name for c in screen.hand) + ")")
            else:
                lines += [_card(card, i) for i, card in enumerate(screen.hand)]
                if screen.selected:
                    lines.append("selected: " + ", ".join(c.name for c in screen.selected))
            return "<screen type=\"HAND_SELECT\">\n" + "\n".join(lines) + "\n</screen>"
        case GameOverScreen():
            outcome = "VICTORY" if screen.victory else "DEFEAT"
            return f"<screen type=\"GAME_OVER\">\n{outcome} | score {screen.score}\n</screen>"
    return None  # NONE screen: combat section carries the content


def _combat_lines(combat: CombatState, deck_names: frozenset[str] = frozenset()) -> str:
    player = combat.player
    lines = [f"turn {combat.turn} | energy {player.energy} | block {player.block}"]
    if combat.card_in_play is not None:
        # A card paused mid-effect (e.g. Survivor awaiting its discard pick):
        # the model should know what is resolving when a selection screen asks.
        lines.append(f"resolving: {combat.card_in_play.name}")
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
        line = "hand" + _card(card, i, in_hand=True)
        if card.name not in deck_names:
            # Generated and inflicted cards (Slimed, Burn, Shiv, ...) are not
            # in the deck briefing; a player reads them off the card face.
            text = card_text(card)
            if text:
                line += f" -- {text}"
        lines.append(line)
    lines.append(
        f"piles: draw {len(combat.draw_pile)}, discard {len(combat.discard_pile)}, "
        f"exhaust {len(combat.exhaust_pile)} (contents via tools)"
    )
    return "\n".join(lines)


def _monster(monster: Monster, index: int) -> str:
    intent = monster.intent.value
    if intent == "DEBUG":
        # The combat's first snapshot can land before intents finish rolling;
        # the wire reports the game's DEBUG placeholder. Distinct from
        # UNKNOWN, which is the game's real question-mark intent.
        intent = "not yet revealed"
    if monster.move_adjusted_damage is not None and monster.move_adjusted_damage >= 0:
        intent += f" {monster.move_adjusted_damage}x{monster.move_hits}"
    powers = ("; " + ", ".join(_power(p) for p in monster.powers)) if monster.powers else ""
    block = f" block {monster.block}" if monster.block else ""
    return (
        f"enemy[{index}] {monster.name} {monster.current_hp}/{monster.max_hp}{block} | intent {intent}{powers}"
    )


def _card(card: Card, index: int | None = None, in_hand: bool = False) -> str:
    # The mod's name already carries upgrade marks ("Strike+", "Searing Blow+2").
    # The playability tags are hand-only: the wire flags reward/shop/grid cards
    # unplayable too (nothing is castable there), but the real screens don't
    # grey them.
    tags = ""
    if in_hand and card.has_target:
        tags += " [needs target]"
    if in_hand and card.is_playable is False:
        tags += " [unplayable]"
    # Cost sentinels: -1 is the X in the energy orb, -2 is no orb at all
    # (statuses, curses) -- neither is a number a player ever sees.
    if card.cost == -1:
        cost = " (X)"
    elif card.cost == -2:
        cost = ""
    else:
        cost = f" ({card.cost})"
    prefix = "" if index is None else f"[{index}] "
    return f"{prefix}{card.name}{cost}{tags}"


def _card_with_text(card: Card, index: int | None = None) -> str:
    line = _card(card, index)
    text = card_text(card)
    return f"{line} -- {text}" if text else line


def _reward(reward, state: GameState) -> str:
    match reward.reward_type:
        case "GOLD":
            label = f"{reward.gold} gold"
        case "STOLEN_GOLD":
            label = f"{reward.gold} gold (stolen back)"
        case "POTION" if reward.potion is not None:
            label = f"potion: {_with_text(reward.potion.name, potion_text(reward.potion))}"
            if state.potions_full:
                label += " (belt full -- discard_potion to make room, or leave it)"
        case "RELIC" if reward.relic is not None:
            label = f"relic: {_with_text(reward.relic.name, relic_text(reward.relic))}"
        case "CARD":
            label = "card (opens a pick of cards, skippable)"
        case "RUBY_KEY" | "EMERALD_KEY" | "SAPPHIRE_KEY" as key:
            label = f"{key.lower().replace('_', ' ')} (one of the 3 keys that unlock the final act)"
        case other:
            label = other.lower().replace("_", " ")
    if reward.link is not None:
        label += f" (linked: taking this forfeits {reward.link.name})"
    return label


def _with_text(name: str, text: str | None) -> str:
    return f"{name} -- {text}" if text else name


def _power(power) -> str:
    # The wire sends -1 for "this power has no stacks"; rendered literally it
    # reads as a negative stack ("Split -1" looks like depleted Strength).
    # Drop it only when the database confirms the sentinel -- a -1 on
    # Strength is real, and a -1 on an unknown power stays visible.
    if amount_is_sentinel(power):
        return power.name
    return f"{power.name} {power.amount}"


def _node(node) -> str:
    return f"{node.symbol or '?'} (x={node.x})"
