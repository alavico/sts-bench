"""Trajectory records: round-trip through JSON, dispatch by discriminator,
and reject shapes the schema does not know -- the same loud-failure stance as
the state schema."""

import pytest
from pydantic import ValidationError

from sts_bench.providers.base import Usage
from sts_bench.trajectory import (
    DecisionRecord,
    FloorRecord,
    FloorScorecard,
    Reward,
    RunOutcome,
    RunRecord,
    RunTotals,
    StateSummary,
    TokenUsage,
    parse_record,
)


def make_run_record(**overrides) -> RunRecord:
    fields = dict(
        run_id="r1",
        started_at="2026-06-11T10:00:00Z",
        seed="STSBENCH1",
        character="ironclad",
        model="gpt-5.4",
        provider_base_url="https://api.openai.com/v1",
        api="responses",
        agent="floor",
    )
    fields.update(overrides)
    return RunRecord(**fields)


def make_floor_record(**overrides) -> FloorRecord:
    fields = dict(
        run_id="r1",
        floor=3,
        floor_type="MonsterRoom",
        conversation=[
            {"role": "system", "content": "you are playing..."},
            {"role": "user", "content": "<state>floor 3</state>"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        ],
        entry=StateSummary(floor=3, hp=68, max_hp=80, gold=142, screen="NONE"),
        exit=StateSummary(floor=3, hp=61, max_hp=80, gold=167, screen="MAP"),
        entry_state={"floor": 3, "current_hp": 68, "gold": 142},
        exit_state={"floor": 3, "current_hp": 61, "gold": 167},
        scorecard=FloorScorecard(hp_delta=-7, gold_delta=25, cards_gained=["Pommel Strike"]),
    )
    fields.update(overrides)
    return FloorRecord(**fields)


def make_decision_record(**overrides) -> DecisionRecord:
    fields = dict(
        run_id="r1",
        floor=3,
        decision_index=17,
        message_start=1,
        message_end=3,
        screen="COMBAT",
        action="play_card 0 (Strike) -> Jaw Worm [0]",
        command="play 1 0",
        rounds=1,
        usage=TokenUsage(prompt_tokens=1200, completion_tokens=40),
    )
    fields.update(overrides)
    return DecisionRecord(**fields)


@pytest.mark.parametrize(
    "make", [make_run_record, make_floor_record, make_decision_record]
)
def test_round_trip_preserves_record(make):
    record = make()
    line = record.model_dump_json()
    assert parse_record(line) == record


def test_parse_record_dispatches_on_discriminator():
    assert isinstance(parse_record(make_run_record().model_dump()), RunRecord)
    assert isinstance(parse_record(make_floor_record().model_dump()), FloorRecord)
    assert isinstance(parse_record(make_decision_record().model_dump()), DecisionRecord)


def test_unknown_record_kind_rejected():
    with pytest.raises(ValidationError):
        parse_record({"record": "episode", "run_id": "r1"})


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        make_decision_record(surprise=True)


def test_message_range_must_be_a_valid_slice():
    with pytest.raises(ValidationError):
        make_decision_record(message_start=5, message_end=3)
    with pytest.raises(ValidationError):
        make_decision_record(message_start=-1, message_end=0)
    # empty range is legal: a decision that added no messages is expressible
    make_decision_record(message_start=4, message_end=4)


def test_token_usage_from_provider_usage():
    usage = Usage(prompt_tokens=100, completion_tokens=30, reasoning_tokens=12)
    converted = TokenUsage.from_usage(usage)
    assert converted == TokenUsage(
        prompt_tokens=100, completion_tokens=30, reasoning_tokens=12, cache_read_tokens=0
    )


def test_records_carry_schema_version():
    record = make_floor_record()
    assert record.schema_version == 1
    assert '"schema_version":1' in record.model_dump_json()


def test_run_record_defaults_reflect_a_run_that_never_finished():
    record = make_run_record()
    assert record.outcome == RunOutcome(victory=None, floor_reached=None, score=None)
    assert record.totals == RunTotals()
    assert record.finished_at is None


def test_floor_reward_is_versioned():
    reward = Reward(spec_version="v1", total=3.5, components={"combat_won": 1.0, "hp_lost": -0.5})
    record = make_floor_record(reward=reward)
    parsed = parse_record(record.model_dump_json())
    assert parsed.reward.spec_version == "v1"
    assert parsed.reward.components["hp_lost"] == -0.5
