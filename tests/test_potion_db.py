"""Pinned potion text resolves for everything the mod can put in front of us."""

import json
from pathlib import Path

from sts_bench.state.schema import EMPTY_POTION_ID, Potion
from sts_bench.tools.potion_db import potion_text

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def make_potion(id, name):
    return Potion.model_validate(
        {"id": id, "name": name, "can_use": True, "can_discard": True, "requires_target": False}
    )


def test_text_by_mod_id():
    assert potion_text(make_potion("Fire Potion", "Fire Potion")) == "Deal 20 damage."


def test_concatenated_variants_keep_the_base_printing():
    # upstream glues Sacred Bark / achievement text behind digit separators
    assert potion_text(make_potion("Attack Potion", "Attack Potion")) == (
        "Choose 1 of 3 random Attack cards to add to your hand, it costs 0 this turn."
    )
    assert potion_text(make_potion("SmokeBomb", "Smoke Bomb")) == (
        "Escape from a non-boss combat. Receive no rewards."
    )


def test_unknown_potion_returns_none():
    assert potion_text(make_potion("Modded Brew", "Modded Brew")) is None


def test_every_fixture_potion_resolves():
    for path in FIXTURES.glob("*.json"):
        game_state = json.loads(path.read_text()).get("game_state") or {}
        pools = [game_state.get("potions", [])]
        screen = game_state.get("screen_state") or {}
        pools.append(screen.get("potions", []))  # shop wares
        for pool in pools:
            for raw in pool:
                potion = Potion.model_validate(raw)
                if potion.id == EMPTY_POTION_ID:
                    continue
                assert potion_text(potion), f"{path.name}: no text for {potion.id}"
