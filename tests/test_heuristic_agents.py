"""Baseline agents: legal-action enumeration and the two provider-free policies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sts_bench.actions import validate
from sts_bench.actions.schema import Choose, PlayCard
from sts_bench.agents.heuristic import RandomAgent, ScriptedAgent, legal_actions, scripted_action
from sts_bench.state import parse_message

FIXTURES = Path(__file__).parent / "fixtures" / "states"
ALL_FIXTURES = sorted(p.stem for p in FIXTURES.glob("*.json"))


def load(name):
    return parse_message(json.loads((FIXTURES / f"{name}.json").read_text()))


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_legal_actions_all_validate(name):
    message = load(name)
    for action in legal_actions(message):
        assert validate(action, message).ok


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_decision_states_offer_at_least_one_action(name):
    message = load(name)
    if "start" in message.available_commands:
        pytest.skip("out-of-game state: the runner handles start outside the agent")
    assert legal_actions(message)


def test_legal_actions_cover_combat():
    message = load("none-1")
    actions = legal_actions(message)
    kinds = {a.kind for a in actions}
    assert "end_turn" in kinds
    combat = message.game_state.combat_state
    playable = {i for i, c in enumerate(combat.hand) if c.is_playable}
    enumerated = {a.card_index for a in actions if a.kind == "play_card"}
    assert enumerated == playable
    # every targeted play points at a live monster
    for action in actions:
        if action.kind == "play_card" and action.target_index is not None:
            monster = combat.monsters[action.target_index]
            assert monster.current_hp > 0 and not monster.is_gone


def test_legal_actions_exclude_unplayable_cards():
    message = load("none-1")
    unplayable = {
        i for i, c in enumerate(message.game_state.combat_state.hand) if c.is_playable is False
    }
    enumerated = {a.card_index for a in legal_actions(message) if a.kind == "play_card"}
    assert not enumerated & unplayable


def test_random_agent_seeded_sequence_is_reproducible():
    messages = [load(name) for name in ("none-1", "map-1", "combat_reward-1")]
    first = [RandomAgent(rng_seed="STSBENCH1").decide(m).action for m in messages]
    second = [RandomAgent(rng_seed="STSBENCH1").decide(m).action for m in messages]
    assert first == second
    assert all(a is not None for a in first)


def test_random_agent_decisions_cost_nothing():
    decision = RandomAgent(rng_seed=7).decide(load("none-1"))
    assert decision.usage.prompt_tokens == 0
    assert decision.usage.completion_tokens == 0
    assert decision.transcript == []
    assert decision.forced_reason is None


def test_scripted_agent_plays_first_playable_card():
    message = load("none-1")
    decision = ScriptedAgent().decide(message)
    expected = scripted_action(message)
    assert isinstance(expected, PlayCard)
    assert decision.action == expected


def test_scripted_agent_takes_first_choice_on_map():
    decision = ScriptedAgent().decide(load("map-1"))
    assert decision.action == Choose(choice_index=0)


def test_scripted_agent_falls_through_when_preferred_pick_is_rejected():
    # A combat-reward state whose first choice is a potion onto a full belt:
    # the validator rejects choice 0, and the agent must advance instead of
    # handing the runner a forced decision.
    raw = json.loads((FIXTURES / "combat_reward-1.json").read_text())
    state = raw["game_state"]
    state["screen_state"]["rewards"].insert(0, {"reward_type": "POTION"})
    state["choice_list"].insert(0, "potion")
    state["potions"] = [
        {"name": "Block Potion", "id": "Block Potion", "can_use": False, "can_discard": True, "requires_target": False},
        {"name": "Block Potion", "id": "Block Potion", "can_use": False, "can_discard": True, "requires_target": False},
    ]
    message = parse_message(raw)
    assert message.game_state.potions_full

    decision = ScriptedAgent().decide(message)
    assert decision.action is not None
    assert decision.action != Choose(choice_index=0)
    assert validate(decision.action, message).ok


def test_heuristic_record_is_a_quiet_no_op():
    agent = ScriptedAgent()
    agent.record("floor 1 COMBAT: play_card 0")  # must not raise; nothing to remember
    assert ScriptedAgent().decide(load("map-1")).action == Choose(choice_index=0)


def test_scripted_action_declines_start_screen():
    assert scripted_action(load("out_of_game-1")) is None
