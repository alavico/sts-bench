"""Pinned card text resolves for everything the mod can put in front of us."""

import json
from pathlib import Path

from sts_bench.state.schema import Card
from sts_bench.tools.card_db import card_text

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def make_card(id, name, upgrades=0):
    return Card.model_validate(
        {
            "id": id,
            "name": name,
            "cost": 1,
            "type": "ATTACK",
            "rarity": "BASIC",
            "upgrades": upgrades,
            "has_target": True,
            "is_playable": True,
            "exhausts": False,
            "ethereal": False,
            "uuid": "test",
        }
    )


def test_base_text_by_mod_id():
    assert card_text(make_card("Strike_R", "Strike")) == "Deal 6 damage."


def test_upgraded_text_when_the_mod_marks_an_upgrade():
    assert card_text(make_card("Strike_R", "Strike+", upgrades=1)) == "Deal 9 damage."


def test_spaced_game_ids_resolve():
    # the game's own card ids can contain spaces; the dataset uses enum style
    assert "X times" in card_text(make_card("Whirlwind", "Whirlwind"))
    assert "any number of times" in card_text(make_card("Searing Blow", "Searing Blow"))


def test_name_fallback_when_the_id_is_unknown():
    assert card_text(make_card("SomeModdedId", "Cleave")) == "Deal 8 damage to ALL enemies."


def test_unknown_card_returns_none():
    assert card_text(make_card("Totally Modded", "Totally Modded")) is None


def test_statuses_and_curses_are_covered():
    assert "Unplayable" in card_text(make_card("Burn", "Burn"))
    assert "Unplayable" in card_text(make_card("Regret", "Regret"))


def test_energy_glyphs_spelled_out_as_counts():
    # in-game orb glyphs ([R] [R]) mean energy; the model gets words
    assert card_text(make_card("Offering", "Offering")) == (
        "Lose 6 HP. Gain 2 Energy. Draw 3 cards. Exhaust."
    )


def test_energy_glyph_as_a_unit_keeps_the_surrounding_amount():
    assert "Costs 1 less Energy" in card_text(make_card("Blood for Blood", "Blood for Blood"))
    assert "gain X Energy" in card_text(make_card("Doppelganger", "Doppelganger"))
    assert "Gain Energy equal to its cost" in card_text(make_card("Recycle", "Recycle"))


def test_generated_cards_come_from_the_supplement():
    # combat-generated cards are absent from the upstream snapshot
    assert card_text(make_card("Shiv", "Shiv")) == "Deal 4 damage. Exhaust."
    assert card_text(make_card("Shiv", "Shiv+", upgrades=1)) == "Deal 6 damage. Exhaust."
    assert card_text(make_card("Miracle", "Miracle+", upgrades=1)) == "Retain. Gain 2 Energy. Exhaust."
    assert "Omega" in card_text(make_card("Beta", "Beta"))
    assert card_text(make_card("Expunger", "Expunger")) == "Deal 9 damage X times."


def test_camelcase_wire_ids_resolve():
    # generated-card ids arrive without separators ("ThroughViolence")
    assert card_text(make_card("ThroughViolence", "Through Violence")) == (
        "Retain. Deal 20 damage. Exhaust."
    )


def test_every_fixture_card_resolves():
    # Player parity: any card the game has actually shown us must have text.
    for path in FIXTURES.glob("*.json"):
        game_state = json.loads(path.read_text()).get("game_state") or {}
        pools = [game_state.get("deck", [])]
        combat = game_state.get("combat_state") or {}
        pools += [combat.get(k, []) for k in ("hand", "draw_pile", "discard_pile")]
        for pool in pools:
            for raw in pool:
                card = Card.model_validate(raw)
                assert card_text(card), f"{path.name}: no text for {card.id} ({card.name})"
