"""Tool registry: action schemas mirror the typed actions; observations answer from state."""

import json
from pathlib import Path

import pytest

from sts_bench.actions.schema import PlayCard
from sts_bench.state import parse_message
from sts_bench.tools import ToolError, ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "states"

ACTION_NAMES = {"play_card", "end_turn", "choose", "use_potion", "discard_potion", "proceed", "return_back"}
OBSERVATION_NAMES = {
    "get_deck",
    "get_draw_pile",
    "get_discard_pile",
    "get_exhaust_pile",
    "get_relics",
    "get_potions",
    "get_map",
    "get_legal_actions",
}


def load(name):
    return parse_message(json.loads((FIXTURES / name).read_text()))


@pytest.fixture
def registry():
    return ToolRegistry()


def test_registry_exposes_all_tools(registry):
    names = {t["function"]["name"] for t in registry.openai_tools()}
    assert names == ACTION_NAMES | OBSERVATION_NAMES


def test_action_schema_drops_kind_and_keeps_fields(registry):
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in registry.openai_tools()}
    play = schemas["play_card"]
    assert "kind" not in play["properties"]
    assert set(play["properties"]) == {"card_index", "target_index"}
    assert play["required"] == ["card_index"]
    assert play["additionalProperties"] is False
    # niladic actions still present a valid object schema
    assert schemas["end_turn"]["properties"] == {}


def test_parse_action_round_trip(registry):
    action = registry.parse_action("play_card", {"card_index": 2, "target_index": 0})
    assert action == PlayCard(card_index=2, target_index=0)


def test_parse_action_rejects_bad_arguments_with_model_facing_message(registry):
    with pytest.raises(ToolError, match="card_index"):
        registry.parse_action("play_card", {"target_index": 0})
    with pytest.raises(ToolError, match="not an action tool"):
        registry.parse_action("get_deck", {})


def test_observation_tools_run_on_every_fixture(registry):
    for path in sorted(FIXTURES.glob("*.json")):
        message = load(path.name)
        for name in OBSERVATION_NAMES:
            text = registry.observe(name, message)
            assert isinstance(text, str) and text


def test_unknown_observation_rejected(registry):
    with pytest.raises(ToolError, match="unknown observation"):
        registry.observe("get_future_rng", load("none-1.json"))


def test_deck_listing_groups_duplicates(registry):
    message = load("none-1.json")
    text = registry.observe("get_deck", message)
    deck = message.game_state.deck
    assert f"deck ({len(deck)} cards)" in text
    strikes = sum(1 for c in deck if c.name == "Strike")
    if strikes > 1:
        assert f"{strikes}x Strike" in text


def test_draw_pile_masks_order(registry):
    message = load("none-1.json")
    text = registry.observe("get_draw_pile", message)
    assert "order unknown" in text
    # grouped lines, not one line per card in pile order
    pile = message.game_state.combat_state.draw_pile
    assert len(text.splitlines()) - 1 <= len(pile)


def test_piles_require_combat(registry):
    assert registry.observe("get_draw_pile", load("map-1.json")) == "not in combat"


def test_map_renders_every_floor_with_edges(registry):
    message = load("map-1.json")
    text = registry.observe("get_map", message)
    floors = {node.y for node in message.game_state.map}
    for y in floors:
        assert f"floor y={y}:" in text
    assert "->" in text
    assert message.game_state.act_boss in text
    # top floor leads to the boss
    assert "BOSS" in text


def test_legal_actions_names_tools_not_wire_commands(registry):
    text = registry.observe("get_legal_actions", load("none-1.json"))
    assert "play_card" in text
    assert "end_turn" in text
    text = registry.observe("get_legal_actions", load("event-1.json"))
    assert "choose" in text


def test_out_of_game_observations_degrade_gracefully(registry):
    message = load("out_of_game-1.json")
    assert registry.observe("get_deck", message) == "not in a run right now"
    assert registry.observe("get_map", message) == "not in a run right now"
