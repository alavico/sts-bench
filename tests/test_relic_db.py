"""Pinned relic text resolves for everything the mod can put in front of us."""

import json
from pathlib import Path

from sts_bench.state.schema import Relic
from sts_bench.tools.relic_db import relic_text

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def make_relic(id, name, counter=-1):
    return Relic.model_validate({"id": id, "name": name, "counter": counter})


def test_text_by_mod_id():
    assert relic_text(make_relic("Burning Blood", "Burning Blood")) == (
        "At the end of combat, heal 6 HP."
    )


def test_corrected_counter_thresholds():
    # upstream lost these numbers; the pinned data carries hand-verified text
    assert "play 10 Attacks" in relic_text(make_relic("Nunchaku", "Nunchaku"))
    assert "Every 3 turns" in relic_text(make_relic("Happy Flower", "Happy Flower"))
    assert relic_text(make_relic("Sundial", "Sundial")) == (
        "Every 3 times you shuffle your draw pile, gain 2 Energy."
    )


def test_energy_glyphs_spelled_out():
    assert relic_text(make_relic("Ancient Tea Set", "Ancient Tea Set")) == (
        "Whenever you enter a Rest Site, start the next combat with 2 Energy."
    )


def test_no_localization_artifacts_survive():
    # upstream leaked literal "LocalizedStrings.PERIOD" into a few entries
    assert relic_text(make_relic("Pear", "Pear")) == "Upon pickup, raise your Max HP by 10."


def test_unknown_relic_returns_none():
    assert relic_text(make_relic("Modded Trinket", "Modded Trinket")) is None


def test_every_fixture_relic_resolves():
    for path in FIXTURES.glob("*.json"):
        game_state = json.loads(path.read_text()).get("game_state") or {}
        for raw in game_state.get("relics", []):
            relic = Relic.model_validate(raw)
            assert relic_text(relic), f"{path.name}: no text for {relic.id} ({relic.name})"
