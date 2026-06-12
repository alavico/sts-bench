"""The JSONL store: ordered round trips, mid-run readability, and the
store-once invariant -- a floor's messages exist in the file exactly once,
inside the floor record; decision records only point at them."""

import json

import pytest
from pydantic import ValidationError

from sts_bench.trajectory import (
    DecisionRecord,
    FloorRecord,
    RunRecord,
    TrajectoryStore,
    read_records,
)

from test_trajectory_schema import (
    make_decision_record,
    make_floor_record,
    make_run_record,
)


def test_round_trip_preserves_records_and_order(tmp_path):
    records = [
        make_decision_record(decision_index=1),
        make_decision_record(decision_index=2),
        make_floor_record(),
        make_run_record(),
    ]
    with TrajectoryStore(tmp_path, "r1") as store:
        for record in records:
            store.append(record)
    assert list(read_records(store.path)) == records


def test_one_file_per_run_named_by_run_id(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectories", "play-20260611-101500")
    store.close()
    assert store.path == tmp_path / "trajectories" / "play-20260611-101500.jsonl"
    assert store.path.exists()


def test_records_are_readable_while_the_run_is_still_going(tmp_path):
    # A crash after any append must leave a valid file: every append lands
    # whole and flushed, never buffered into a torn tail.
    with TrajectoryStore(tmp_path, "r1") as store:
        store.append(make_decision_record(decision_index=1))
        assert [r.decision_index for r in read_records(store.path)] == [1]
        store.append(make_decision_record(decision_index=2))
        assert [r.decision_index for r in read_records(store.path)] == [1, 2]


def test_reading_a_corrupt_line_fails_loudly(tmp_path):
    path = tmp_path / "r1.jsonl"
    path.write_text(make_run_record().model_dump_json() + "\n" + '{"record": "mystery"}\n')
    with pytest.raises(ValidationError):
        list(read_records(path))


def test_store_once_each_message_lands_in_the_file_exactly_once(tmp_path):
    # The floor record owns the conversation; its decisions hold index ranges.
    # Every message body must appear once in the raw file -- a second copy
    # means some record is duplicating instead of pointing.
    conversation = [
        {"role": "system", "content": "marker-system-prompt"},
        {"role": "user", "content": "marker-digest-decision-1"},
        {"role": "assistant", "content": "marker-response-decision-1"},
        {"role": "tool", "tool_call_id": "c1", "content": "marker-result-decision-2"},
        {"role": "user", "content": "marker-digest-decision-2"},
        {"role": "assistant", "content": "marker-response-decision-2"},
    ]
    floor = make_floor_record(conversation=conversation)
    decisions = [
        make_decision_record(decision_index=1, message_start=0, message_end=3),
        make_decision_record(decision_index=2, message_start=3, message_end=6),
    ]
    with TrajectoryStore(tmp_path, "r1") as store:
        for decision in decisions:
            store.append(decision)
        store.append(floor)
        store.append(make_run_record())

    raw = store.path.read_text()
    for message in conversation:
        assert raw.count(message["content"]) == 1

    # and the pointers reach exactly the messages the floor record owns
    floors = [r for r in read_records(store.path) if isinstance(r, FloorRecord)]
    stored_decisions = [r for r in read_records(store.path) if isinstance(r, DecisionRecord)]
    covered = []
    for decision in stored_decisions:
        covered.extend(floors[0].conversation[decision.message_start : decision.message_end])
    assert covered == conversation


def test_every_line_is_standalone_json(tmp_path):
    # JSONL contract: any line can be grepped out and parsed on its own.
    with TrajectoryStore(tmp_path, "r1") as store:
        store.append(make_decision_record())
        store.append(make_floor_record())
        store.append(make_run_record())
    lines = store.path.read_text().splitlines()
    assert len(lines) == 3
    kinds = [json.loads(line)["record"] for line in lines]
    assert kinds == ["decision", "floor", "run"]


def test_run_record_last_marks_a_finished_run(tmp_path):
    # The run record is appended at the end; a file without one reads as a
    # run that died mid-flight, floors and decisions intact.
    with TrajectoryStore(tmp_path, "crashed") as store:
        store.append(make_decision_record())
    records = list(read_records(store.path))
    assert not any(isinstance(r, RunRecord) for r in records)
    assert len(records) == 1
