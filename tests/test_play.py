"""Runner safeguards and narration: loop guard, action descriptions, landmarks."""

import json
from pathlib import Path

from sts_bench.actions import Choose, DiscardPotion, PlayCard, Proceed, ReturnBack
from sts_bench.play import (
    LoopGuard,
    _auto_api,
    _model_traffic,
    _screen_label,
    _transition_notes,
    describe_action,
)
from sts_bench.state import parse_message

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def load(name):
    return parse_message(json.loads((FIXTURES / name).read_text()))


def raw_state(name):
    return json.loads((FIXTURES / name).read_text())["game_state"]


def test_loop_guard_trips_on_alternating_screen_cycle():
    # The shop loop: SHOP_ROOM -> choose, SHOP_SCREEN -> leave, repeat. The
    # repeats are never back-to-back, so the guard must count per pair, not
    # consecutively.
    guard = LoopGuard(limit=3)
    room, screen = {"screen_type": "SHOP_ROOM"}, {"screen_type": "SHOP_SCREEN"}
    assert not guard.trips(room, "choose 0")
    assert not guard.trips(screen, "leave")
    assert not guard.trips(room, "choose 0")
    assert not guard.trips(screen, "leave")
    assert guard.trips(room, "choose 0")


def test_loop_guard_ignores_repeats_when_the_state_moved():
    guard = LoopGuard(limit=3)
    for hp in range(40, 20, -2):  # same command, monster losing HP each time
        assert not guard.trips({"screen_type": "NONE", "hp": hp}, "play 1 0")


def test_loop_guard_distinguishes_commands_on_the_same_state():
    guard = LoopGuard(limit=3)
    state = {"screen_type": "SHOP_ROOM"}
    assert not guard.trips(state, "choose 0")
    assert not guard.trips(state, "proceed")
    assert not guard.trips(state, "choose 0")
    assert not guard.trips(state, "proceed")


def test_describe_play_card_names_card_and_target():
    message = load("none-1.json")  # hand[3] is Strike, monster 0 is Jaw Worm
    desc = describe_action(PlayCard(card_index=3, target_index=0), message)
    assert desc == "play_card 3 (Strike) -> Jaw Worm [0]"


def test_describe_choose_names_the_choice():
    message = load("card_reward-1.json")  # choices: fusion, barrage, compile driver
    assert describe_action(Choose(choice_index=1), message) == "choose 1 (barrage)"


def test_describe_potion_names_the_slot():
    message = load("card_reward-1.json")  # slot 0 holds a Strength Potion
    desc = describe_action(DiscardPotion(slot_index=0), message)
    assert desc == "discard_potion 0 (Strength Potion)"


def test_describe_proceed_is_quiet_when_the_wire_verb_matches():
    # combat_reward offers literal `proceed`, so no alias annotation is needed
    assert describe_action(Proceed(), load("combat_reward-1.json")) == "proceed"


def test_describe_return_back_shows_the_wire_alias():
    # On the shop screen the back action goes out as `leave`; the narration
    # should say so instead of the opaque kind alone.
    message = load("shop_screen-1.json")
    assert describe_action(ReturnBack(), message) == "return_back (leave)"


def test_transition_notes_floor_and_combat_start():
    prev = {"floor": 1, "act": 1, "screen_type": "COMBAT_REWARD"}
    cur = {
        "floor": 2,
        "act": 1,
        "screen_type": "NONE",
        "combat_state": {"turn": 1, "monsters": [{"name": "Cultist", "is_gone": False}]},
    }
    assert _transition_notes(prev, cur) == [
        "entering floor 2 (act 1): COMBAT",
        "combat starts vs Cultist",
    ]


def test_screen_label_renames_combat_none():
    assert _screen_label({"screen_type": "NONE", "combat_state": {"turn": 1}}) == "COMBAT"
    assert _screen_label({"screen_type": "NONE"}) == "NONE"
    assert _screen_label({"screen_type": "SHOP_ROOM"}) == "SHOP_ROOM"


def test_transition_notes_turn_change_and_combat_over():
    in_turn_1 = {"floor": 2, "combat_state": {"turn": 1, "monsters": []}}
    in_turn_2 = {"floor": 2, "combat_state": {"turn": 2, "monsters": []}}
    after = {"floor": 2, "screen_type": "COMBAT_REWARD"}
    assert _transition_notes(in_turn_1, in_turn_2) == ["combat turn 2"]
    assert _transition_notes(in_turn_2, after) == ["combat over"]
    assert _transition_notes(in_turn_1, in_turn_1) == []


def test_transition_notes_quiet_outside_a_run():
    assert _transition_notes({}, {}) == []


def test_auto_api_prefers_native_surface_per_backend(monkeypatch):
    for var in ("STS_BENCH_BASE_URL", "STS_BENCH_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    # explicit base URL decides directly
    assert _auto_api("https://api.anthropic.com/v1") == "anthropic"
    assert _auto_api("http://localhost:11434/v1") == "chat"

    # no base URL: an Anthropic key selects the native messages api
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert _auto_api(None) == "anthropic"

    # an explicit generic key means an unnamed compat backend
    monkeypatch.setenv("STS_BENCH_API_KEY", "sk-other")
    assert _auto_api(None) == "chat"


def test_model_traffic_tags_directions_and_skips_system():
    transcript = [
        {"role": "system", "content": "you are playing..."},
        {"role": "user", "content": "<run>floor 1</run>"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_deck", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "10 cards"},
        {"role": "assistant", "content": "I will strike.", "reasoning_content": "deck is thin"},
    ]
    assert _model_traffic(transcript) == [
        (">m", "<run>floor 1</run>"),
        ("<m", "call get_deck {}"),
        (">m", "[c1] 10 cards"),
        ("<m", "(reasoning) deck is thin"),
        ("<m", "I will strike."),
    ]
