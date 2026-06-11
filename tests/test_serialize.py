"""The cursory view renders every fixture and surfaces what an agent must see."""

import json
from pathlib import Path

import pytest

from sts_bench.state import parse_message
from sts_bench.state.serialize import cursory_view

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "states").glob("*.json"))


def render(path):
    return cursory_view(parse_message(json.loads(path.read_text())))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_renders_without_error(path):
    text = render(path)
    assert "<commands>" in text
    # The cursory view must stay cursory: full snapshots are ~20KB of JSON.
    assert len(text) < 4000, f"cursory view too large ({len(text)} chars)"


def test_combat_view_has_essentials():
    path = Path(__file__).parent / "fixtures" / "states" / "none-1.json"
    raw = json.loads(path.read_text())
    text = render(path)
    combat = raw["game_state"]["combat_state"]
    assert "energy" in text
    for monster in combat["monsters"]:
        if not monster["is_gone"]:
            assert monster["name"] in text
    for card in combat["hand"]:
        assert card["name"] in text
    # pile contents must NOT be dumped -- only counts
    assert f"draw {len(combat['draw_pile'])}" in text


def test_map_view_mentions_next_nodes():
    path = Path(__file__).parent / "fixtures" / "states" / "map-1.json"
    text = render(path)
    assert "MAP" in text
    assert "next:" in text or "boss" in text


def test_out_of_game_view():
    path = Path(__file__).parent / "fixtures" / "states" / "out_of_game-1.json"
    text = render(path)
    assert "out of game" in text
    assert "start" in text


def load_raw(name):
    return json.loads((Path(__file__).parent / "fixtures" / "states" / name).read_text())


def fill_potions(raw):
    raw["game_state"]["potions"] = [
        {"id": n, "name": n, "can_use": True, "can_discard": True, "requires_target": False}
        for n in ("Block Potion", "Strength Potion", "Dexterity Potion")
    ]


def test_run_line_counts_potion_slots():
    raw = load_raw("card_reward-1.json")  # one Strength Potion, two empty slots
    assert "potions (1/3): Strength Potion" in cursory_view(parse_message(raw))

    fill_potions(raw)
    assert "potions (3/3, full):" in cursory_view(parse_message(raw))


def test_potion_capacity_follows_the_wire_not_a_constant():
    # The mod sizes the potions array to the player's real slot count (empty
    # slots arrive as "Potion Slot" placeholders), so ascension 11's 2 slots
    # and Potion Belt's +2 must fall out of the data with no hardcoded 3.
    raw = load_raw("card_reward-1.json")
    potion = raw["game_state"]["potions"][0]  # the held Strength Potion
    empty = {"id": "Potion Slot", "name": "Potion Slot", "can_use": False,
             "can_discard": False, "requires_target": False}

    raw["game_state"]["potions"] = [potion, empty]  # ascension 11+: 2 slots
    assert "potions (1/2): Strength Potion" in cursory_view(parse_message(raw))
    raw["game_state"]["potions"] = [potion, potion]
    assert "potions (2/2, full):" in cursory_view(parse_message(raw))

    raw["game_state"]["potions"] = [potion] * 2 + [empty] * 3  # Potion Belt: 5 slots
    assert "potions (2/5): " in cursory_view(parse_message(raw))


def test_combat_reward_renders_loot_not_a_pick_one_menu():
    text = render(Path(__file__).parent / "fixtures" / "states" / "combat_reward-1.json")
    assert "take each in any order" in text
    assert "[0] 11 gold" in text
    assert "[1] potion: Strength Potion -- Gain 2 Strength." in text
    assert "[2] card (opens a pick of cards, skippable)" in text
    # the duplicate keyword listing is gone: one indexed rendering only
    assert "<choices>" not in text


def test_combat_reward_potion_warns_when_belt_is_full():
    raw = load_raw("combat_reward-1.json")
    fill_potions(raw)
    text = cursory_view(parse_message(raw))
    assert "belt full -- discard_potion to make room" in text


def test_combat_reward_empty_says_all_claimed():
    raw = load_raw("combat_reward-1.json")
    raw["game_state"]["screen_state"]["rewards"] = []
    raw["game_state"].pop("choice_list", None)
    text = cursory_view(parse_message(raw))
    assert "all loot claimed -- proceed" in text


def test_hand_select_complete_stops_offering_indices():
    raw = load_raw("hand_select-1.json")
    screen = raw["game_state"]["screen_state"]
    screen["selected"] = [screen["hand"].pop(0)]  # 1 of max 1 picked
    raw["game_state"].pop("choice_list", None)
    raw["game_state"]["room_phase"] = raw["game_state"].get("room_phase", "COMBAT")
    raw["available_commands"] = ["potion", "confirm", "key", "click", "wait", "state"]
    text = cursory_view(parse_message(raw))
    assert "selection complete (1/1) -- proceed to confirm" in text
    assert "[0]" not in text.split("<combat>")[0]  # no dead indices on the screen


def test_rest_after_resting_points_at_proceed():
    raw = load_raw("combat_reward-1.json")
    raw["game_state"]["screen_type"] = "REST"
    raw["game_state"]["screen_state"] = {"has_rested": True, "rest_options": []}
    raw["game_state"].pop("choice_list", None)
    raw["available_commands"] = ["potion", "proceed", "key", "click", "wait", "state"]
    text = cursory_view(parse_message(raw))
    assert "already rested -- proceed" in text


def test_commands_line_speaks_tool_names():
    raw = load_raw("shop_screen-1.json")  # wire offers: choose, potion, leave
    text = cursory_view(parse_message(raw))
    assert "<commands>choose, use_potion, discard_potion, return_back (leave)</commands>" in text


def test_shop_listing_has_no_indices_that_clash_with_choices():
    # choice_list numbering starts at purge, so per-category [i] tags would lie
    text = render(Path(__file__).parent / "fixtures" / "states" / "shop_screen-1.json")
    shop = text.split("</screen>")[0]
    assert "card: " in shop and "[0]" not in shop


def test_card_reward_shows_printed_text():
    text = render(Path(__file__).parent / "fixtures" / "states" / "card_reward-1.json")
    assert "[0] Fusion" in text and "Channel 1 Plasma." in text


def test_shop_cards_show_printed_text():
    text = render(Path(__file__).parent / "fixtures" / "states" / "shop_screen-1.json")
    assert "Warcry" in text and "Draw 1 card" in text  # Warcry's printed effect


def test_relic_bar_lists_text_and_counters():
    from sts_bench.state.serialize import relic_bar

    raw = load_raw("none-1.json")
    raw["game_state"]["relics"] = [
        {"id": "Cracked Core", "name": "Cracked Core", "counter": -1},
        {"id": "Nunchaku", "name": "Nunchaku", "counter": 7},
    ]
    text = relic_bar(parse_message(raw).game_state)
    assert text.startswith("<relic_bar>")
    assert "Cracked Core: At the start of each combat, Channel 1 Lightning." in text
    assert "Nunchaku (counter 7): Every time you play 10 Attacks, gain 1 Energy." in text


def test_combat_briefing_is_relics_then_potions_then_deck():
    from sts_bench.state.serialize import combat_briefing

    state = parse_message(load_raw("card_reward-1.json")).game_state  # holds a potion
    briefing = combat_briefing(state)
    assert (
        briefing.index("<relic_bar>")
        < briefing.index("<potion_belt>")
        < briefing.index("<deck_reference>")
    )
    assert "Strength Potion: Gain 2 Strength." in briefing


def test_empty_belt_is_omitted_from_the_briefing():
    from sts_bench.state.serialize import combat_briefing

    state = parse_message(load_raw("none-1.json")).game_state  # all slots empty
    assert "<potion_belt>" not in combat_briefing(state)


def test_shop_and_boss_offerings_show_hover_text():
    text = render(Path(__file__).parent / "fixtures" / "states" / "shop_screen-1.json")
    assert "potion: Speed Potion -- " in text  # text between name and price

    raw = load_raw("combat_reward-1.json")
    raw["game_state"]["screen_type"] = "BOSS_REWARD"
    raw["game_state"]["screen_state"] = {
        "relics": [{"id": "Sozu", "name": "Sozu", "counter": -1}]
    }
    raw["game_state"].pop("choice_list", None)
    text = cursory_view(parse_message(raw))
    assert "[0] Sozu -- Gain 1 Energy at the start of your turn. You can no longer obtain Potions." in text


def test_rolling_intent_placeholder_renders_honestly():
    raw = load_raw("none-1.json")
    raw["game_state"]["combat_state"]["monsters"][0]["intent"] = "DEBUG"
    text = cursory_view(parse_message(raw))
    assert "intent not yet revealed" in text
    assert "DEBUG" not in text


def test_deck_reference_groups_counts_and_text():
    from sts_bench.state.serialize import deck_reference

    state = parse_message(load_raw("none-1.json")).game_state
    text = deck_reference(state)
    assert text.startswith("<deck_reference>")
    assert "Strike x3 (1): Deal 6 damage." in text
    # the upgraded copy lists separately, with upgraded text and no doubled mark
    assert "Strike+ (1): Deal 9 damage." in text
    assert "Strike++" not in text
