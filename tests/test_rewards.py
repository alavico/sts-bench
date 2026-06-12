"""Floor rewards from raw boundary states: each signal fires on the delta
that defines it, and a stored floor record re-scores to the same number."""

import json
from pathlib import Path

import pytest

from sts_bench.env import REWARD_SPEC_VERSION, floor_reward

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def state(**fields) -> dict:
    base = {
        "floor": 5,
        "current_hp": 60,
        "max_hp": 80,
        "gold": 100,
        "room_type": "MonsterRoom",
        "screen_type": "NONE",
    }
    base.update(fields)
    return base


def test_cleared_combat_floor_scores_advance_combat_and_hp():
    entry = state()
    exit_ = state(floor=6, current_hp=44, gold=120, screen_type="MAP")
    reward = floor_reward(entry, exit_)
    assert reward.spec_version == REWARD_SPEC_VERSION
    assert reward.components == {
        "floor_advanced": 1.0,
        "combat_won": 2.0,
        "hp": -1.0,  # 16 of 80 max HP
    }
    assert reward.total == pytest.approx(2.0)


def test_non_combat_floor_advancing_scores_no_combat():
    entry = state(room_type="ShopRoom", gold=200)
    exit_ = state(room_type="ShopRoom", floor=6, gold=50)
    reward = floor_reward(entry, exit_)
    assert "combat_won" not in reward.components
    assert reward.components["floor_advanced"] == 1.0


def test_gold_is_a_metric_not_a_reward():
    # Rewarding the gold delta would punish spending it at shops; gold rides
    # the floor scorecard instead and never appears in the reward.
    entry = state(gold=300)
    exit_ = state(floor=6, gold=20)  # spent big at the shop
    assert "gold" not in floor_reward(entry, exit_).components


def test_death_floor_scores_only_the_damage():
    entry = state(current_hp=22)
    exit_ = state(
        current_hp=0,
        screen_type="GAME_OVER",
        screen_state={"victory": False, "score": 214},
    )
    reward = floor_reward(entry, exit_)
    assert reward.components == {"hp": pytest.approx(-1.375)}
    assert reward.total < 0


def test_victory_counts_the_final_combat_as_won():
    # The winning kill ends the run on the same floor: no floor advance,
    # but the boss room was survived and the run is won.
    entry = state(floor=51, room_type="MonsterRoomBoss", current_hp=30)
    exit_ = state(
        floor=51,
        current_hp=12,
        screen_type="GAME_OVER",
        screen_state={"victory": True, "score": 1200},
    )
    reward = floor_reward(entry, exit_)
    assert reward.components["run_won"] == 50.0
    assert reward.components["combat_won"] == 2.0
    assert "floor_advanced" not in reward.components


def test_unchanged_signals_are_omitted_not_zero():
    entry = state(room_type="RestRoom")
    exit_ = state(room_type="RestRoom")
    reward = floor_reward(entry, exit_)
    assert reward.components == {}
    assert reward.total == 0.0


def test_real_captured_state_scores_without_error():
    raw = json.loads((FIXTURES / "shop_screen-1.json").read_text())["game_state"]
    after = dict(raw, floor=raw["floor"] + 1, gold=raw["gold"] - 150)
    reward = floor_reward(raw, after)
    assert reward.components == {"floor_advanced": 1.0}


def test_rescoring_stored_boundary_states_is_deterministic():
    entry = state()
    exit_ = state(floor=6, current_hp=44, gold=120)
    first = floor_reward(entry, exit_)
    again = floor_reward(json.loads(json.dumps(entry)), json.loads(json.dumps(exit_)))
    assert again == first
