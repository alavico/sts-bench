"""Validator and translator against real captured states."""

import json
from pathlib import Path

import pytest

from sts_bench.actions import (
    Choose,
    DiscardPotion,
    EndTurn,
    PlayCard,
    Proceed,
    ReturnBack,
    UsePotion,
    translate,
    validate,
)
from sts_bench.state import parse_message

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def load(name):
    return parse_message(json.loads((FIXTURES / f"{name}.json").read_text()))


@pytest.fixture(scope="module")
def combat():
    return load("none-1")


@pytest.fixture(scope="module")
def map_screen():
    return load("map-1")


def first_playable(message, needs_target):
    hand = message.game_state.combat_state.hand
    for i, card in enumerate(hand):
        if card.is_playable and card.has_target == needs_target:
            return i, card
    pytest.skip(f"fixture has no playable card with has_target={needs_target}")


def test_play_card_accept_and_translate(combat):
    index, card = first_playable(combat, needs_target=False)
    action = PlayCard(card_index=index)
    assert validate(action, combat).ok
    assert translate(action, combat) == f"play {index + 1}"  # wire format is 1-based


def test_play_targeted_card_requires_target(combat):
    index, card = first_playable(combat, needs_target=True)
    verdict = validate(PlayCard(card_index=index), combat)
    assert not verdict.ok
    assert "target" in verdict.reason

    action = PlayCard(card_index=index, target_index=0)
    assert validate(action, combat).ok
    assert translate(action, combat) == f"play {index + 1} 0"


def test_play_card_out_of_range(combat):
    hand_size = len(combat.game_state.combat_state.hand)
    verdict = validate(PlayCard(card_index=hand_size), combat)
    assert not verdict.ok
    assert "out of range" in verdict.reason


def test_untargeted_card_rejects_target(combat):
    index, card = first_playable(combat, needs_target=False)
    verdict = validate(PlayCard(card_index=index, target_index=0), combat)
    assert not verdict.ok
    assert "does not take a target" in verdict.reason


def test_end_turn_in_combat(combat):
    assert validate(EndTurn(), combat).ok
    assert translate(EndTurn(), combat) == "end"


def test_combat_rejects_screen_actions(combat):
    assert not validate(Choose(choice_index=0), combat).ok
    assert not validate(Proceed(), combat).ok


def test_choose_on_map(map_screen):
    n_choices = len(map_screen.game_state.choice_list)
    assert n_choices > 0
    action = Choose(choice_index=0)
    assert validate(action, map_screen).ok
    assert translate(action, map_screen) == "choose 0"

    verdict = validate(Choose(choice_index=n_choices), map_screen)
    assert not verdict.ok
    assert "choices are" in verdict.reason


def test_map_rejects_combat_actions(map_screen):
    assert not validate(PlayCard(card_index=0), map_screen).ok
    assert not validate(EndTurn(), map_screen).ok


def test_return_on_map(map_screen):
    verdict = validate(ReturnBack(), map_screen)
    commands = set(map_screen.available_commands)
    assert verdict.ok == bool(commands.intersection(("return", "skip", "cancel", "leave")))


def test_potion_validation(combat):
    potions = combat.game_state.potions
    usable = next((i for i, p in enumerate(potions) if p.can_use), None)
    if "potion" not in combat.available_commands or usable is None:
        verdict = validate(UsePotion(slot_index=0), combat)
        assert not verdict.ok
        return
    potion = potions[usable]
    action = UsePotion(slot_index=usable, target_index=0 if potion.requires_target else None)
    assert validate(action, combat).ok


def test_discard_potion_out_of_range(combat):
    n = len(combat.game_state.potions)
    verdict = validate(DiscardPotion(slot_index=n + 5), combat)
    assert not verdict.ok


def full_belt(raw):
    raw["game_state"]["potions"] = [
        {"id": n, "name": n, "can_use": True, "can_discard": True, "requires_target": False}
        for n in ("Block Potion", "Strength Potion", "Dexterity Potion")
    ]
    return parse_message(raw)


def test_potion_reward_rejected_when_belt_is_full():
    raw = json.loads((FIXTURES / "combat_reward-1.json").read_text())
    message = full_belt(raw)
    # choice 1 is the Strength Potion reward; the game cannot take it and
    # CommunicationMod hangs on the click, so the validator must stop it
    verdict = validate(Choose(choice_index=1), message)
    assert not verdict.ok
    assert "belt is full" in verdict.reason and "discard_potion" in verdict.reason
    # the other loot stays claimable
    assert validate(Choose(choice_index=0), message).ok
    assert validate(Choose(choice_index=2), message).ok


def test_potion_reward_allowed_with_a_free_slot():
    message = load("combat_reward-1")  # two empty slots in the fixture
    assert validate(Choose(choice_index=1), message).ok


def test_shop_potion_rejected_when_belt_is_full():
    raw = json.loads((FIXTURES / "shop_screen-1.json").read_text())
    message = full_belt(raw)
    choices = message.game_state.choice_list
    potion_index = choices.index("speed potion")
    verdict = validate(Choose(choice_index=potion_index), message)
    assert not verdict.ok
    assert "belt is full" in verdict.reason
    # cards are still buyable
    assert validate(Choose(choice_index=choices.index("warcry")), message).ok


def test_belt_capacity_comes_from_the_wire_not_a_constant():
    # ascension 11+ runs have 2 slots; full at 2 must reject just like full at 3
    raw = json.loads((FIXTURES / "combat_reward-1.json").read_text())
    raw["game_state"]["potions"] = [
        {"id": n, "name": n, "can_use": True, "can_discard": True, "requires_target": False}
        for n in ("Block Potion", "Strength Potion")
    ]
    verdict = validate(Choose(choice_index=1), parse_message(raw))
    assert not verdict.ok
    assert "(2/2)" in verdict.reason
