"""Replay planning: turn a trajectory into an ordered command list and a stop
point, the pure half of the live driver (the driving itself needs a game)."""

import pytest

from sts_bench.drive import PlanError, command_divergence, entry_diffs, plan_replay

from test_trajectory_schema import make_decision_record, make_floor_record, make_run_record


def make_records(**run_overrides):
    """Three floors of decisions: a small but realistic run to plan against.

    Decisions 1-2 on floor 1, 3-4 on floor 2, 5 on floor 3, each carrying a
    distinct wire command so order is checkable.
    """
    run = make_run_record(**run_overrides)
    floors = [
        make_floor_record(floor=1, entry_state={"floor": 1, "current_hp": 80}),
        make_floor_record(floor=2, entry_state={"floor": 2, "current_hp": 72}),
        make_floor_record(floor=3, entry_state={"floor": 3, "current_hp": 68}),
    ]
    decisions = [
        make_decision_record(decision_index=1, floor=1, command="play 1 0"),
        make_decision_record(decision_index=2, floor=1, command="end"),
        make_decision_record(decision_index=3, floor=2, command="choose 0"),
        make_decision_record(decision_index=4, floor=2, command="proceed"),
        make_decision_record(decision_index=5, floor=3, command="choose 1"),
    ]
    return [run, *floors, *decisions]


def test_until_decision_replays_everything_before_the_target():
    plan = plan_replay(make_records(), until_decision=4)
    # decisions 1-3 lead to the state decision 4 faced; 4 itself is not played
    assert plan.commands == ["play 1 0", "end", "choose 0"]
    assert plan.target_index == 4
    assert plan.target_floor == 2
    assert plan.target_decision is not None
    assert plan.target_decision.decision_index == 4


def test_inclusive_also_plays_the_target_decision():
    plan = plan_replay(make_records(), until_decision=4, inclusive=True)
    assert plan.commands == ["play 1 0", "end", "choose 0", "proceed"]


def test_until_floor_stops_at_the_first_decision_on_that_floor():
    plan = plan_replay(make_records(), until_floor=2)
    # floor 2's first decision is 3; replay everything before it
    assert plan.commands == ["play 1 0", "end"]
    assert plan.target_index == 3
    assert plan.target_floor == 2


def test_run_config_and_floor_entries_come_from_the_records():
    plan = plan_replay(make_records(seed="DAILY7", character="silent", ascension=5), until_decision=1)
    assert (plan.seed, plan.character, plan.ascension) == ("DAILY7", "silent", 5)
    assert plan.commands == []  # nothing precedes decision 1
    assert plan.floor_entries[2] == {"floor": 2, "current_hp": 72}


def test_exactly_one_selector_is_required():
    with pytest.raises(PlanError, match="exactly one"):
        plan_replay(make_records())
    with pytest.raises(PlanError, match="exactly one"):
        plan_replay(make_records(), until_decision=1, until_floor=2)


def test_out_of_range_decision_is_rejected():
    with pytest.raises(PlanError, match="out of range"):
        plan_replay(make_records(), until_decision=99)


def test_unknown_floor_lists_the_floors_that_exist():
    with pytest.raises(PlanError, match=r"floors with decisions: \[1, 2, 3\]"):
        plan_replay(make_records(), until_floor=14)


def test_missing_run_record_cannot_be_driven():
    records = [r for r in make_records() if not hasattr(r, "seed")]
    with pytest.raises(PlanError, match="no run record"):
        plan_replay(records, until_decision=1)


def test_unparsed_states_are_surfaced_for_the_caller_to_warn():
    from sts_bench.trajectory import RunTotals

    records = make_records(totals=RunTotals(unparsed_states=2))
    plan = plan_replay(records, until_floor=3)
    assert plan.unparsed_states == 2


def test_in_range_decision_without_a_command_is_counted_not_played():
    records = make_records()
    # blank decision 2's command: it should be skipped and counted
    for r in records:
        if hasattr(r, "decision_index") and r.decision_index == 2:
            r.command = None
    plan = plan_replay(records, until_decision=4)
    assert plan.commands == ["play 1 0", "choose 0"]
    assert plan.missing_commands == 1


def _state(deck=None, relics=None, **fields):
    s = {"floor": 6, "current_hp": 50, "max_hp": 70, "gold": 99, "screen_type": "NONE"}
    s.update(fields)
    s["deck"] = [{"name": n.rstrip("+"), "upgrades": 1 if n.endswith("+") else 0} for n in (deck or [])]
    s["relics"] = [{"name": n} for n in (relics or [])]
    return s


def test_entry_diffs_clean_when_states_match():
    a = _state(deck=["Strike", "Strike", "Bash"], relics=["Burning Blood"])
    b = _state(deck=["Bash", "Strike", "Strike"], relics=["Burning Blood"])  # order-insensitive
    assert entry_diffs(a, b) == []


def test_entry_diffs_flags_vitals():
    diffs = entry_diffs(_state(current_hp=40), _state(current_hp=50))
    assert any("current_hp" in d for d in diffs)


def test_entry_diffs_flags_deck_divergence_with_the_delta():
    recorded = _state(deck=["Strike", "Strike", "Cleave"])
    live = _state(deck=["Strike", "Strike", "Bash"])  # Cleave swapped for Bash
    diffs = entry_diffs(live, recorded)
    assert any("deck 3->3" in d and "-Cleave" in d and "+Bash" in d for d in diffs)


def test_entry_diffs_flags_missing_relic_and_upgrades():
    recorded = _state(deck=["Strike+"], relics=["Burning Blood", "Anchor"])
    live = _state(deck=["Strike"], relics=["Burning Blood"])
    diffs = entry_diffs(live, recorded)
    assert any("deck" in d and "-Strike+" in d and "+Strike" in d for d in diffs)
    assert any("relics" in d and "-Anchor" in d for d in diffs)


def _combat(hand):
    return {"combat_state": {"hand": [{"name": n} for n in hand]}}


def test_command_divergence_catches_choose_picking_a_different_card():
    # the exact Sentry-fight failure: recorded took cleave, live offers rampage
    d = make_decision_record(action="choose 1 (cleave)", command="choose 1")
    gs = {"choice_list": ["pummel", "rampage", "sever soul"]}
    msg = command_divergence(d, gs)
    assert msg and "cleave" in msg and "rampage" in msg


def test_command_divergence_clean_when_choice_matches():
    d = make_decision_record(action="choose 1 (cleave)", command="choose 1")
    gs = {"choice_list": ["havoc", "cleave", "thunderclap"]}
    assert command_divergence(d, gs) is None


def test_command_divergence_catches_play_on_a_diverged_hand():
    # the wrong card can still be playable -- the game wouldn't reject it
    d = make_decision_record(action="play_card 4 (Cleave) -> Sentry [0]", command="play 5 0")
    gs = _combat(["Ascender's Bane", "Spot Weakness", "Defend+", "Strike", "Rampage"])
    msg = command_divergence(d, gs)
    assert msg and "cleave" in msg and "rampage" in msg


def test_command_divergence_clean_when_hand_matches():
    d = make_decision_record(action="play_card 0 (Strike) -> Jaw Worm [0]", command="play 1 0")
    gs = _combat(["Strike", "Defend"])
    assert command_divergence(d, gs) is None


def test_command_divergence_ignores_skip_and_unindexable_actions():
    skip = make_decision_record(action="choose 0 (skip)", command="choose 0")
    assert command_divergence(skip, {"choice_list": ["take this card"]}) is None
    proceed = make_decision_record(action="proceed", command="proceed")
    assert command_divergence(proceed, {}) is None


def test_command_divergence_safe_when_index_out_of_range():
    # never invent a divergence from a state we can't index
    d = make_decision_record(action="play_card 4 (Cleave)", command="play 5")
    assert command_divergence(d, _combat(["Strike"])) is None
