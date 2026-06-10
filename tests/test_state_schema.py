"""Every harvested fixture must parse, strictly. New fixtures join automatically."""

import json
from pathlib import Path

import pytest

from sts_bench.state import ScreenType, StateParseError, parse_message

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "states").glob("*.json"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_parses(path):
    message = parse_message(json.loads(path.read_text()))
    assert message.ready_for_command is not None
    if message.in_game:
        assert message.game_state is not None
        assert message.game_state.screen is not None


def test_combat_fixture_semantics():
    raw = json.loads((Path(__file__).parent / "fixtures" / "states" / "none-1.json").read_text())
    state = parse_message(raw).game_state
    assert state.combat_state is not None
    combat = state.combat_state
    assert combat.player.energy >= 0
    assert combat.monsters, "combat fixture should have monsters"
    assert all(m.max_hp > 0 for m in combat.monsters)
    assert state.screen_type == ScreenType.NONE


def test_out_of_game_has_no_game_state():
    raw = json.loads((Path(__file__).parent / "fixtures" / "states" / "out_of_game-1.json").read_text())
    message = parse_message(raw)
    assert not message.in_game
    assert message.game_state is None
    assert "start" in message.available_commands


def test_unknown_field_fails_loudly():
    raw = json.loads((Path(__file__).parent / "fixtures" / "states" / "out_of_game-1.json").read_text())
    raw["brand_new_field"] = 123
    with pytest.raises(StateParseError) as excinfo:
        parse_message(raw)
    assert excinfo.value.raw is raw  # payload preserved for harvesting


def test_unmodeled_screen_fails_loudly():
    raw = json.loads((Path(__file__).parent / "fixtures" / "states" / "map-1.json").read_text())
    raw["game_state"]["screen_type"] = "SHOP_SCREEN"
    raw["game_state"]["screen_state"] = {}
    with pytest.raises(StateParseError, match="no model yet"):
        parse_message(raw)
