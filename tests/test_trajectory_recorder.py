"""The recorder turns loop events into records, and the floor record is a
faithful packet: driven through the real floor agent, the stored conversation
is byte-for-byte what the provider saw on the wire."""

import json
from pathlib import Path

from sts_bench.agents import FloorAgent
from sts_bench.providers.base import Usage
from sts_bench.state import parse_message
from sts_bench.trajectory import (
    DecisionRecord,
    FloorRecord,
    RunRecord,
    RunRecorder,
    TrajectoryStore,
    read_records,
)
from sts_bench.agents.base import Decision

from test_floor_agent import ScriptedProvider, tool_response

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def load_raw(name):
    return json.loads((FIXTURES / name).read_text())


def make_recorder(tmp_path, run_id="r1"):
    store = TrajectoryStore(tmp_path, run_id)
    recorder = RunRecorder(
        store,
        run_id=run_id,
        seed="STSBENCH1",
        character="ironclad",
        ascension=0,
        model="test-model",
        provider_base_url="http://localhost",
        api="chat",
        reasoning_effort=None,
        agent="floor",
    )
    return store, recorder


def fake_decision(transcript, forced_reason=None):
    return Decision(
        action=None,
        forced_reason=forced_reason,
        rounds=1,
        usage=Usage(prompt_tokens=100, completion_tokens=10),
        transcript=transcript,
    )


def records_by_kind(path):
    records = list(read_records(path))
    return (
        [r for r in records if isinstance(r, DecisionRecord)],
        [r for r in records if isinstance(r, FloorRecord)],
        [r for r in records if isinstance(r, RunRecord)],
    )


def state(floor, **fields):
    base = {
        "floor": floor,
        "current_hp": 60,
        "max_hp": 80,
        "gold": 100,
        "room_type": "MonsterRoom",
        "screen_type": "NONE",
    }
    base.update(fields)
    return base


def test_floor_boundary_flushes_a_floor_record_with_scorecard_and_reward(tmp_path):
    store, recorder = make_recorder(tmp_path)
    recorder.observe(state(1, deck=[{"name": "Strike"}]))
    recorder.decision(
        fake_decision([{"role": "user", "content": "d1"}, {"role": "assistant", "content": "a1"}]),
        screen="COMBAT",
        action="end_turn",
        command="end",
        latency_ms=1200,
    )
    recorder.observe(state(1, current_hp=50, gold=120, deck=[{"name": "Strike"}, {"name": "Bash"}]))
    recorder.observe(state(2, current_hp=50, gold=120, deck=[{"name": "Strike"}, {"name": "Bash"}]))
    recorder.finish()
    store.close()

    decisions, floors, runs = records_by_kind(store.path)
    assert len(floors) == 2  # floor 1 at the boundary, floor 2 at finish
    first = floors[0]
    assert first.floor == 1
    assert first.floor_type == "MonsterRoom"
    assert first.conversation == [
        {"role": "user", "content": "d1"},
        {"role": "assistant", "content": "a1"},
    ]
    assert first.entry.hp == 60 and first.exit.hp == 50
    assert first.scorecard.hp_delta == -10
    assert first.scorecard.gold_delta == 20
    assert first.scorecard.cards_gained == ["Bash"]
    assert first.reward.components["combat_won"] == 2.0
    assert first.entry_state["floor"] == 1  # raw boundary states ride along

    assert decisions[0].floor == 1
    assert decisions[0].message_start == 0 and decisions[0].message_end == 2
    assert decisions[0].latency_ms == 1200


def test_decision_ranges_restart_with_each_floors_conversation(tmp_path):
    store, recorder = make_recorder(tmp_path)
    recorder.observe(state(1))
    recorder.decision(fake_decision([{"role": "user", "content": "f1-d1"}] * 3),
                      screen="COMBAT", action="a", command="c")
    recorder.observe(state(2))
    recorder.decision(fake_decision([{"role": "user", "content": "f2-d1"}] * 2),
                      screen="MAP", action="a", command="c")
    recorder.finish()
    store.close()

    decisions, floors, _ = records_by_kind(store.path)
    assert [(d.message_start, d.message_end) for d in decisions] == [(0, 3), (0, 2)]
    assert [len(f.conversation) for f in floors] == [3, 2]


def test_game_over_state_sets_the_outcome(tmp_path):
    store, recorder = make_recorder(tmp_path)
    recorder.observe(state(14))
    recorder.observe(
        state(14, current_hp=0, screen_type="GAME_OVER", screen_state={"victory": False, "score": 214})
    )
    record = recorder.finish()
    store.close()
    assert record.outcome.victory is False
    assert record.outcome.floor_reached == 14
    assert record.outcome.score == 214


def test_totals_count_decisions_forced_unparsed_and_tokens(tmp_path):
    store, recorder = make_recorder(tmp_path)
    recorder.observe(state(1))
    recorder.decision(fake_decision([]), screen="COMBAT", action="a", command="c")
    recorder.decision(fake_decision([], forced_reason="loop guard"),
                      screen="COMBAT", action=None, command="end")
    recorder.unparsed_state()
    record = recorder.finish()
    store.close()
    assert record.totals.decisions == 2
    assert record.totals.forced == 1
    assert record.totals.unparsed_states == 1
    assert record.totals.usage.prompt_tokens == 200
    assert record.finished_at is not None


def test_finish_runs_once(tmp_path):
    store, recorder = make_recorder(tmp_path)
    recorder.observe(state(1))
    assert recorder.finish() is recorder.finish()
    store.close()
    _, floors, runs = records_by_kind(store.path)
    assert len(floors) == 1 and len(runs) == 1


def test_combat_turns_track_the_deepest_turn_seen(tmp_path):
    store, recorder = make_recorder(tmp_path)
    recorder.observe(state(1, combat_state={"turn": 1}))
    recorder.observe(state(1, combat_state={"turn": 4}))
    recorder.observe(state(2))
    recorder.finish()
    store.close()
    _, floors, _ = records_by_kind(store.path)
    assert floors[0].scorecard.combat_turns == 4
    assert floors[1].scorecard.combat_turns is None


def test_packet_property_floor_conversation_is_what_the_wire_saw(tmp_path):
    # Drive the real floor agent across a floor boundary and record every
    # decision delta. The stored floor conversation must equal the provider's
    # final request on that floor plus the final response -- nothing missing,
    # nothing extra, nothing stored twice.
    responses = [
        tool_response(("play_card", {"card_index": 3, "target_index": 0})),
        tool_response(("end_turn", {})),
        tool_response(("return_back", {})),  # floor 4: shop
    ]
    provider = ScriptedProvider(list(responses))
    agent = FloorAgent(provider)
    store, recorder = make_recorder(tmp_path)

    combat_raw = load_raw("none-1.json")  # floor 1
    shop_raw = load_raw("shop_screen-1.json")  # floor 4
    for raw, outcome in (
        (combat_raw, "floor 1 COMBAT: play_card 3 (Strike) -> Jaw Worm [0]"),
        (combat_raw, "floor 1 COMBAT: end_turn"),
        (shop_raw, "floor 4 SHOP_SCREEN: return_back"),
    ):
        game_state = raw["game_state"]
        recorder.observe(game_state)
        decision = agent.decide(parse_message(raw))
        recorder.decision(
            decision,
            screen="COMBAT",
            action=outcome,
            command="x",
            latency_ms=1,
        )
        agent.record(outcome)
    recorder.finish()
    store.close()

    decisions, floors, _ = records_by_kind(store.path)
    assert [f.floor for f in floors] == [1, 4]

    # wire truth for floor 1: the last request the provider saw there, plus
    # the assistant message that answered it
    wire = provider.requests[1]["messages"] + [responses[1].message]
    assert floors[0].conversation == wire

    # the decision ranges tile the conversation exactly: no gaps, no overlap
    floor1_decisions = [d for d in decisions if d.floor == 1]
    rebuilt = []
    for d in floor1_decisions:
        rebuilt.extend(floors[0].conversation[d.message_start : d.message_end])
    assert rebuilt == floors[0].conversation

    # floor 4 opens fresh: system prompt plus the digest carrying the summary
    assert floors[1].conversation[0]["role"] == "system"
    assert "<previous_floor>" in floors[1].conversation[1]["content"]
