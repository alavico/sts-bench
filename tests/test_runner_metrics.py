"""Metrics and the comparison report, from synthetic trajectory records."""

from __future__ import annotations

from sts_bench.env.rewards import Reward
from sts_bench.runner import SUITES, RunMetrics, aggregate, comparison_report, hash_drift
from sts_bench.trajectory import (
    DecisionRecord,
    FloorRecord,
    RunOutcome,
    RunRecord,
    TokenUsage,
)
from sts_bench.trajectory.schema import FloorScorecard, StateSummary


def make_run(
    run_id="run-1",
    seed="STSBENCH1",
    model="test-model",
    agent="floor",
    effort: str | None = None,
    prompt_hash: str | None = "p1",
    tool_hash: str | None = "t1",
    victory=False,
    floor_reached=12,
):
    return RunRecord(
        run_id=run_id,
        started_at="2026-06-12T00:00:00+00:00",
        finished_at="2026-06-12T00:30:00+00:00",
        seed=seed,
        character="ironclad",
        ascension=0,
        model=model,
        provider_base_url="http://test",
        api="chat",
        reasoning_effort=effort,
        agent=agent,
        prompt_hash=prompt_hash,
        tool_schema_hash=tool_hash,
        outcome=RunOutcome(victory=victory, floor_reached=floor_reached, score=200),
    )


def make_floor(
    run_id="run-1",
    floor=1,
    reward_total=3.0,
    *,
    gold_delta=0,
    potions_gained=(),
    entry_gold: int | None = None,
    exit_gold: int | None = None,
):
    return FloorRecord(
        run_id=run_id,
        floor=floor,
        scorecard=FloorScorecard(gold_delta=gold_delta, potions_gained=list(potions_gained)),
        entry=StateSummary(gold=entry_gold),
        exit=StateSummary(gold=exit_gold),
        reward=Reward(spec_version="v1", total=reward_total, components={"floor_advanced": reward_total}),
    )


def make_decision(
    run_id="run-1",
    index=1,
    floor=1,
    *,
    invalid_actions=0,
    forced_reason: str | None = None,
    observation_calls=0,
    latency_ms: int | None = 2000,
    screen: str | None = None,
    action: str | None = None,
    command: str | None = None,
):
    return DecisionRecord(
        run_id=run_id,
        floor=floor,
        decision_index=index,
        message_start=0,
        message_end=0,
        invalid_actions=invalid_actions,
        forced_reason=forced_reason,
        observation_calls=observation_calls,
        latency_ms=latency_ms,
        screen=screen,
        action=action,
        command=command,
        usage=TokenUsage(prompt_tokens=1000, completion_tokens=50, reasoning_tokens=20),
    )


def synthetic_records():
    return [
        make_decision(index=1, floor=1),
        make_decision(index=2, floor=1, invalid_actions=2),
        make_floor(floor=1, reward_total=3.0),
        make_decision(index=3, floor=2, forced_reason="loop guard", latency_ms=None),
        make_decision(index=4, floor=2, observation_calls=3),
        make_floor(floor=2, reward_total=1.5),
        make_run(floor_reached=12),
    ]


def test_run_metrics_from_records():
    metrics = RunMetrics.from_records(synthetic_records())
    assert metrics.run_id == "run-1"
    assert metrics.model == "test-model"
    assert metrics.complete
    assert metrics.victory is False
    assert metrics.floor_reached == 12
    assert metrics.decisions == 4
    assert metrics.forced == 1
    assert metrics.invalid_actions == 2
    assert metrics.invalid_decisions == 1
    assert metrics.invalid_decision_rate == 0.25
    assert metrics.observation_calls == 3
    assert metrics.prompt_tokens == 4000
    assert metrics.completion_tokens == 200
    assert metrics.reasoning_tokens == 80
    assert metrics.latencies_ms == [2000, 2000, 2000]  # the forced decision had none
    assert metrics.reward_total == 4.5


def strategic_records():
    """A run that takes one of three card rewards, drinks one of two potions,
    and spends 60 of the 100 gold it earned."""
    return [
        make_decision(index=1, floor=1, screen="CARD_REWARD", command="choose 0", action="choose 0 (bash)"),
        make_decision(index=2, floor=1, screen="CARD_REWARD", command="skip", action="skip"),
        make_floor(floor=1, gold_delta=40, potions_gained=["Fire Potion"], exit_gold=139),
        make_decision(index=3, floor=2, screen="CARD_REWARD", command="return", action="return_back"),
        make_decision(index=4, floor=2, action="use_potion 0 (Fire Potion)", command="potion use 0"),
        make_floor(floor=2, gold_delta=60, potions_gained=["Block Potion"], exit_gold=199),
        make_floor(floor=3, gold_delta=-60, exit_gold=139),
        make_run(floor_reached=3),
    ]


def test_strategic_metrics_from_records():
    metrics = RunMetrics.from_records(strategic_records())
    assert metrics.card_reward_decisions == 3
    assert metrics.card_skips == 2  # "skip" and "return" decline; "choose 0" takes
    assert metrics.skip_rate == 2 / 3
    assert metrics.potions_gained == 2
    assert metrics.potions_used == 1
    assert metrics.potion_use_rate == 0.5
    assert metrics.gold_earned == 100
    assert metrics.gold_spent == 60
    assert metrics.gold_spent_ratio == 0.6
    assert metrics.gold_final == 139


def test_gold_spent_ratio_counts_the_starting_purse():
    # The run opens with 99 gold; spending it is not overspending, so the
    # ratio is spent over everything available, never more than 100%.
    records = [
        make_floor(floor=1, gold_delta=-50, entry_gold=99, exit_gold=49),
        make_run(floor_reached=2),
    ]
    metrics = RunMetrics.from_records(records)
    assert metrics.gold_start == 99
    assert metrics.gold_spent == 50
    assert metrics.gold_spent_ratio == 50 / 99


def test_potion_used_without_a_recorded_gain_counts_as_gained():
    # Entropic Brew's products are born and drunk within a single floor, so
    # they never cross a floor boundary into a scorecard. The use itself
    # proves the potion existed; the rate must never exceed 100%.
    records = [
        make_decision(index=1, floor=1, action="use_potion 0 (Attack Potion)", command="potion use 0"),
        make_floor(floor=1),
        make_run(floor_reached=2),
    ]
    metrics = RunMetrics.from_records(records)
    assert metrics.potions_used == 1
    assert metrics.potions_gained == 1
    assert metrics.potion_use_rate == 1.0


def test_starting_belt_potions_count_as_gained():
    # A potion held before floor 1 (a Neow gift) is in no scorecard diff, but
    # drinking it must not read as using more potions than were ever owned.
    floor = FloorRecord(
        run_id="run-1",
        floor=1,
        entry_state={"potions": [{"name": "Fire Potion"}, {"name": "Potion Slot"}]},
        scorecard=FloorScorecard(),
        exit=StateSummary(),
    )
    drink = make_decision(index=1, floor=1, action="use_potion 0 (Fire Potion)")
    metrics = RunMetrics.from_records([drink, floor, make_run()])
    assert metrics.potions_gained == 1
    assert metrics.potions_used == 1
    assert metrics.potion_use_rate == 1.0


def test_strategic_metrics_default_to_none_without_data():
    metrics = RunMetrics.from_records([make_run()])
    assert metrics.skip_rate is None
    assert metrics.potion_use_rate is None
    assert metrics.gold_spent_ratio is None
    assert metrics.gold_final is None


def test_aggregate_strategic_rates_pool_counts_not_rates():
    # 0/3 skips in one run, 2/3 in another: the pooled rate is 2/6, not the
    # mean of per-run rates (runs with more card rewards weigh more).
    never = RunMetrics.from_records([
        make_decision(index=i, floor=1, screen="CARD_REWARD", command="choose 0")
        for i in (1, 2, 3)
    ] + [make_run(run_id="a", seed="S1")])
    sometimes = RunMetrics.from_records(strategic_records())
    agg = aggregate([never, sometimes])[0]
    assert agg.skip_rate == 2 / 6
    assert agg.potion_use_rate == 0.5
    assert agg.gold_spent_ratio == 0.6


def test_config_table_carries_strategic_columns():
    report = comparison_report([RunMetrics.from_records(strategic_records())])
    assert "| card skips | potions used | gold spent |" in report
    assert "| 67% | 50% | 60% |" in report


def test_median_floor_resists_a_lucky_seed():
    # Four runs wall at 16, one reaches 33: the mean (19.4) overstates a
    # configuration whose median run dies at the act 1 boss.
    runs = [
        RunMetrics.from_records([make_run(run_id=f"r{i}", seed=f"S{i}", floor_reached=f)])
        for i, f in enumerate((16, 16, 16, 16, 33))
    ]
    agg = aggregate(runs)[0]
    assert agg.median_floor == 16
    assert agg.mean_floor == 19.4
    report = comparison_report(runs)
    assert "| floor mean | floor median | floor best |" in report
    assert "tokens/run (in+out)" in report


def test_run_without_run_record_is_incomplete():
    records = synthetic_records()[:-1]  # the run died before its closing record
    metrics = RunMetrics.from_records(records)
    assert not metrics.complete
    assert metrics.model == "?"
    assert metrics.victory is None
    assert metrics.floor_reached == 2  # best the floors prove
    assert metrics.decisions == 4  # the decision stream still counts


def test_aggregate_groups_by_configuration():
    run_a1 = RunMetrics.from_records([make_run(run_id="a1", seed="S1")])
    run_a2 = RunMetrics.from_records([make_run(run_id="a2", seed="S2", victory=True, floor_reached=51)])
    run_b = RunMetrics.from_records([make_run(run_id="b1", model="other-model")])
    aggregates = aggregate([run_a1, run_a2, run_b])
    assert [agg.n for agg in aggregates] == [2, 1]
    first = aggregates[0]
    assert first.wins == 1
    assert first.win_rate == 0.5
    assert first.mean_floor == 31.5
    assert first.best_floor == 51
    assert not hash_drift(aggregates)


def test_prompt_hash_change_splits_the_row_and_warns():
    before = RunMetrics.from_records([make_run(run_id="a1", prompt_hash="p1")])
    after = RunMetrics.from_records([make_run(run_id="a2", prompt_hash="p2")])
    aggregates = aggregate([before, after])
    assert len(aggregates) == 2
    warnings = hash_drift(aggregates)
    assert len(warnings) == 1
    assert "test-model" in warnings[0]


def test_comparison_report_renders():
    runs = [
        RunMetrics.from_records(synthetic_records()),
        RunMetrics.from_records(
            [make_run(run_id="run-2", seed="STSBENCH2", victory=True, floor_reached=51)]
        ),
        RunMetrics.from_records(
            [make_run(run_id="run-3", model="baseline-random", agent="random", prompt_hash=None, tool_hash=None, floor_reached=4)]
        ),
    ]
    report = comparison_report(
        runs, suite=SUITES["smoke"], pricing={"test-model": (0.25, 2.0)}
    )
    assert "Suite **smoke**" in report
    assert "STSBENCH1, STSBENCH2" in report
    assert "| test-model | 2 | 1 |" in report
    assert "**51 W**" in report  # the win, marked in the by-seed grid
    assert "baseline-random" in report  # labels name the model, never the agent
    assert "$" in report  # priced model gets a cost
    assert "VICTORY" in report and "DEFEAT" in report


def test_report_cost_blank_without_pricing():
    runs = [RunMetrics.from_records([make_run()])]
    report = comparison_report(runs)
    row = next(line for line in report.splitlines() if line.startswith("| test-model"))
    assert row.rstrip().endswith("| - |")


def test_report_from_files_combines_sessions(tmp_path):
    # Baselines recorded in one session, the model run in another: the
    # combined report reads both files as if they had run together.
    from sts_bench.runner.reports import report_from_files
    from sts_bench.trajectory import TrajectoryStore

    paths = []
    for run_id, model, agent in (
        ("baseline-day", "scripted", "scripted"),
        ("model-day", "test-model", "floor"),
    ):
        with TrajectoryStore(tmp_path, run_id=run_id) as store:
            store.append(make_run(run_id=run_id, model=model, agent=agent))
        paths.append(tmp_path / f"{run_id}.jsonl")

    report = report_from_files(paths, suite=SUITES["smoke"])
    assert "| scripted | 1 |" in report  # baseline label collapses model/agent
    assert "| test-model | 1 |" in report
    assert "Suite **smoke**" in report


def test_effort_distinguishes_configurations():
    low = RunMetrics.from_records([make_run(run_id="a", effort="low")])
    high = RunMetrics.from_records([make_run(run_id="b", effort="high")])
    aggregates = aggregate([low, high])
    assert len(aggregates) == 2
    assert {agg.key.label for agg in aggregates} == {
        "test-model effort=low",
        "test-model effort=high",
    }
    assert not hash_drift(aggregates)  # different efforts are different configs, not drift
