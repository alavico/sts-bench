"""Replay from the JSONL alone: a run recorded through the real floor agent
re-renders complete, and the packet verifier catches every way the store
could break its contract."""

import json
from pathlib import Path

from sts_bench.agents import FloorAgent
from sts_bench.replay import group, render_run, verify, verify_floor
from sts_bench.state import parse_message
from sts_bench.trajectory import RunRecorder, TrajectoryStore, read_records

from test_floor_agent import ScriptedProvider, tool_response
from test_trajectory_schema import make_decision_record, make_floor_record

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def record_two_floor_run(tmp_path):
    """A real run through the floor agent: two combat decisions on floor 1,
    one shop decision on floor 4, recorded as the play loop would."""
    provider = ScriptedProvider(
        [
            tool_response(("play_card", {"card_index": 3, "target_index": 0})),
            tool_response(("end_turn", {})),
            tool_response(("return_back", {})),
        ]
    )
    agent = FloorAgent(provider)
    store = TrajectoryStore(tmp_path, "replay-test")
    recorder = RunRecorder(
        store,
        run_id="replay-test",
        seed="STSBENCH1",
        character="ironclad",
        ascension=0,
        model="test-model",
        provider_base_url="http://localhost",
        api="chat",
        reasoning_effort=None,
        agent="floor",
    )
    combat = json.loads((FIXTURES / "none-1.json").read_text())
    shop = json.loads((FIXTURES / "shop_screen-1.json").read_text())
    steps = [
        (combat, "play_card 3 (Strike) -> Jaw Worm [0]", "play 4 0"),
        (combat, "end_turn", "end"),
        (shop, "return_back (leave)", "leave"),
    ]
    for raw, action, command in steps:
        recorder.observe(raw["game_state"])
        decision = agent.decide(parse_message(raw))
        recorder.decision(decision, screen="COMBAT", action=action, command=command, latency_ms=5)
        agent.record(action)
    recorder.finish()
    store.close()
    return store.path


def test_replayed_run_verifies_and_renders_every_channel(tmp_path):
    path = record_two_floor_run(tmp_path)
    records = list(read_records(path))

    assert verify(records) == []

    text = "\n".join(render_run(records))
    # run header and outcome from the run record
    assert "run replay-test | test-model" in text
    assert "outcome: UNFINISHED" in text
    # the system prompt appears exactly once
    assert text.count("You are playing Slay the Spire") == 1
    # floor headers carry the scorecard and the versioned reward
    assert "=== floor 1 (MonsterRoom)" in text
    assert "=== floor 4 (ShopRoom)" in text
    assert "reward v1" in text
    # each decision line and its conversation slice are rendered
    assert "decision 1 (COMBAT): play_card 3 (Strike) -> Jaw Worm [0]" in text
    assert ">m <run>" in text or "<run>" in text
    assert "call play_card" in text
    assert "call end_turn" in text
    assert "!!" not in text  # no violations flagged


def test_floor_filter_renders_only_that_floor(tmp_path):
    path = record_two_floor_run(tmp_path)
    text = "\n".join(render_run(list(read_records(path)), only_floor=4))
    assert "=== floor 4" in text
    assert "=== floor 1" not in text


def test_verifier_catches_gap_overlap_and_orphan_messages():
    floor = make_floor_record()  # 3-message conversation
    ok = [
        make_decision_record(decision_index=1, message_start=0, message_end=2),
        make_decision_record(decision_index=2, message_start=2, message_end=3),
    ]
    assert verify_floor(floor, ok) == []

    gap = [make_decision_record(decision_index=1, message_start=1, message_end=3)]
    assert any("gap" in p for p in verify_floor(floor, gap))

    overlap = [
        make_decision_record(decision_index=1, message_start=0, message_end=2),
        make_decision_record(decision_index=2, message_start=1, message_end=3),
    ]
    assert any("overlap" in p for p in verify_floor(floor, overlap))

    uncovered = [make_decision_record(decision_index=1, message_start=0, message_end=2)]
    assert any("no decision" in p for p in verify_floor(floor, uncovered))


def test_run_without_run_record_renders_as_died_mid_flight():
    records = [
        make_decision_record(message_start=0, message_end=3),
        make_floor_record(),
    ]
    text = "\n".join(render_run(records))
    assert "died mid-flight" in text
    assert "=== floor 3" in text


def test_decisions_without_their_floor_record_are_flagged():
    # A hard crash can leave decisions whose floor never flushed.
    records = [make_decision_record(floor=7, message_start=0, message_end=2)]
    run, floors, decisions = group(records)
    assert run is None and floors == []
    text = "\n".join(render_run(records))
    assert "floor 7: 1 decisions but no floor record" in text
