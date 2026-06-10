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
