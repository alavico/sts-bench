import pytest

from sts_bench.resume import ResumeError, load_checkpoint, pending_action_call_id, rewrite_checkpoint
from sts_bench.trajectory import RunRecorder, TrajectoryStore, read_records
from sts_bench.trajectory.schema import TokenUsage

from test_trajectory_schema import make_decision_record, make_floor_record, make_run_record


def write_records(path, records):
    path.write_text("".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8")


def test_checkpoint_prunes_terminal_run_and_current_floor(tmp_path):
    path = tmp_path / "play-1.jsonl"
    prior_floor = make_floor_record(floor=2)
    current_floor = make_floor_record(
        floor=3,
        conversation=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "state"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "end_turn", "arguments": "{}"}}
                ],
            },
        ],
    )
    d1 = make_decision_record(decision_index=1, floor=2)
    d2 = make_decision_record(decision_index=2, floor=3, message_start=1, message_end=3)
    run = make_run_record()
    write_records(path, [d1, prior_floor, d2, current_floor, run])

    checkpoint = load_checkpoint(path)

    assert checkpoint.run == run
    assert checkpoint.current_floor == current_floor
    assert checkpoint.records_to_keep == [d1, prior_floor, d2]
    assert checkpoint.pruned_records == [run, current_floor]


def test_checkpoint_at_floor_boundary_keeps_completed_floor_packet(tmp_path):
    path = tmp_path / "play-1.jsonl"
    d1 = make_decision_record(decision_index=1, floor=3)
    completed_floor = make_floor_record(floor=3, conversation=[{"role": "user", "content": "done"}])
    next_floor_closeout = make_floor_record(
        floor=4,
        conversation=[],
        entry_state={"floor": 4, "current_hp": 60},
        exit_state={"floor": 4, "current_hp": 60},
    )
    run = make_run_record()
    write_records(path, [d1, completed_floor, next_floor_closeout, run])

    checkpoint = load_checkpoint(path)

    assert checkpoint.current_floor == next_floor_closeout
    assert checkpoint.records_to_keep == [d1, completed_floor]
    assert checkpoint.pruned_records == [run, next_floor_closeout]


def test_checkpoint_refuses_completed_run(tmp_path):
    path = tmp_path / "play-1.jsonl"
    write_records(
        path,
        [
            make_decision_record(decision_index=1, floor=3),
            make_floor_record(floor=3),
            make_run_record(outcome={"victory": False, "floor_reached": 3}),
        ],
    )

    with pytest.raises(ResumeError, match="game over"):
        load_checkpoint(path)


def test_rewrite_checkpoint_makes_backup_and_removes_closeout_records(tmp_path):
    path = tmp_path / "play-1.jsonl"
    d1 = make_decision_record(decision_index=1, floor=3)
    floor = make_floor_record(floor=3)
    run = make_run_record()
    write_records(path, [d1, floor, run])
    checkpoint = load_checkpoint(path)

    backup = rewrite_checkpoint(path, checkpoint)

    assert backup is not None and backup.exists()
    assert list(read_records(path)) == [d1]
    assert list(read_records(backup)) == [d1, floor, run]


def test_pending_action_call_id_ignores_answered_observation_calls():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "obs", "type": "function", "function": {"name": "get_deck", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "obs", "content": "deck"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "act", "type": "function", "function": {"name": "play_card", "arguments": "{}"}}
            ],
        },
    ]

    assert pending_action_call_id(messages) == "act"


def test_pending_action_call_id_reads_anthropic_native_blocks():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "_blocks": [
                {"type": "tool_use", "id": "toolu_1", "name": "end_turn", "input": {}}
            ],
        }
    ]

    assert pending_action_call_id(messages) == "toolu_1"


def test_recorder_resume_continues_indices_and_totals(tmp_path):
    store = TrajectoryStore(tmp_path, "play-1")
    run = make_run_record(
        run_id="play-1",
        started_at="2026-06-25T01:02:03Z",
        totals={"unparsed_states": 2},
    )
    decisions = [
        make_decision_record(
            run_id="play-1",
            decision_index=1,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=1, cache_read_tokens=4),
        ),
        make_decision_record(
            run_id="play-1",
            decision_index=2,
            forced_reason="loop guard",
            usage=TokenUsage(prompt_tokens=20, completion_tokens=2, reasoning_tokens=1),
        ),
    ]
    floor = make_floor_record(
        run_id="play-1",
        floor=3,
        conversation=[{"role": "user", "content": "old"}],
        entry_state={"floor": 3, "current_hp": 70},
    )
    recorder = RunRecorder.resume(
        store,
        run=run,
        decisions=decisions,
        current_floor=floor,
        checkpoint_state={"floor": 3, "current_hp": 66},
    )
    record = recorder.finish()
    store.close()

    assert record.started_at == "2026-06-25T01:02:03Z"
    assert record.totals.decisions == 2
    assert record.totals.forced == 1
    assert record.totals.unparsed_states == 2
    assert record.totals.usage.prompt_tokens == 30
    assert record.totals.usage.completion_tokens == 3
    assert record.totals.usage.reasoning_tokens == 1
    assert record.totals.usage.cache_read_tokens == 4
