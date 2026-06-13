"""The comparison report: one markdown table set per seed suite.

Rows are configurations (model / scaffold / effort), never single runs --
the suite is the unit of comparison, and runs whose prompt or tool-schema
hashes differ stay in separate rows with a warning saying why. Everything
renders from `RunMetrics`, so a report is recomputable from the trajectory
files alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .metrics import RunMetrics, SuiteAggregate, aggregate, hash_drift
from .seeds import Suite

# $ per million tokens (input, output), keyed by exact model name. Cost is
# reported only for models listed here; everything else shows "-" rather
# than a stale guess.
Pricing = dict[str, tuple[float, float]]


def comparison_report(
    runs: list[RunMetrics], *, suite: Suite | None = None, pricing: Pricing | None = None
) -> str:
    aggregates = aggregate(runs)
    lines: list[str] = ["# Benchmark report", ""]
    if suite is not None:
        lines += [
            f"Suite **{suite.name}**: {suite.character} ascension {suite.ascension}, "
            f"seeds {', '.join(suite.seeds)}.",
            "",
        ]
    for warning in hash_drift(aggregates):
        lines.append(f"> ⚠ {warning}")
    if hash_drift(aggregates):
        lines.append("")

    lines += _config_table(aggregates, pricing or {})
    lines += _floor_by_seed(aggregates)
    lines += _run_table(runs)
    return "\n".join(lines) + "\n"


def report_from_files(
    paths: Iterable[Path], *, suite: Suite | None = None, pricing: Pricing | None = None
) -> str:
    """One report over any set of trajectory files, in the order given.

    Runs are sessions' artifacts, the report is a view: rerun it over any
    mix of bench and play trajectories -- baselines recorded last week,
    model runs from today -- and configurations aggregate exactly as if they
    had run in one session.
    """
    return comparison_report(
        [RunMetrics.from_file(Path(path)) for path in paths],
        suite=suite,
        pricing=pricing,
    )


def _config_table(aggregates: list[SuiteAggregate], pricing: Pricing) -> list[str]:
    header = (
        "| configuration | runs | wins | floor mean | floor best | reward mean "
        "| invalid dec. | forced | lookups/dec | latency p50 | tokens/run | cost/run |"
    )
    rows = [header, "|" + "---|" * 12]
    for agg in aggregates:
        prompt, completion = agg.mean_tokens_per_run
        rows.append(
            "| {label} | {n} | {wins} | {floor} | {best} | {reward} | {invalid} "
            "| {forced} | {lookups} | {latency} | {tokens} | {cost} |".format(
                label=agg.key.label,
                n=agg.n,
                wins=agg.wins,
                floor=_num(agg.mean_floor),
                best=_num(agg.best_floor),
                reward=_num(agg.mean_reward),
                invalid=f"{agg.invalid_decision_rate:.1%}",
                forced=f"{agg.forced_rate:.1%}",
                lookups=f"{agg.observation_calls_per_decision:.2f}",
                latency=_latency(agg.median_latency_ms),
                tokens=f"{prompt:,.0f}+{completion:,.0f}",
                cost=_cost(agg, pricing),
            )
        )
    return rows + [""]


def _floor_by_seed(aggregates: list[SuiteAggregate]) -> list[str]:
    """Floor reached, configuration x seed: the suite's results at a glance.
    A win is marked alongside its floor; unfinished runs show "?"."""
    seeds = []
    for agg in aggregates:
        for run in agg.runs:
            seed = run.seed or "(random)"
            if seed not in seeds:
                seeds.append(seed)
    if not seeds:
        return []
    rows = [
        "## Floor reached by seed",
        "",
        "| configuration | " + " | ".join(seeds) + " |",
        "|" + "---|" * (len(seeds) + 1),
    ]
    for agg in aggregates:
        cells = []
        for seed in seeds:
            marks = [
                _floor_mark(run)
                for run in agg.runs
                if (run.seed or "(random)") == seed
            ]
            cells.append(" / ".join(marks) if marks else "-")
        rows.append(f"| {agg.key.label} | " + " | ".join(cells) + " |")
    return rows + [""]


def _run_table(runs: list[RunMetrics]) -> list[str]:
    rows = [
        "## Runs",
        "",
        "| run | configuration | seed | outcome | floor | score | decisions "
        "| forced | invalid | tokens | reward |",
        "|" + "---|" * 11,
    ]
    for run in runs:
        rows.append(
            "| {id} | {label} | {seed} | {outcome} | {floor} | {score} | {decisions} "
            "| {forced} | {invalid} | {tokens} | {reward} |".format(
                id=run.run_id,
                label=run.config.label,
                seed=run.seed or "(random)",
                outcome=_outcome(run),
                floor=_num(run.floor_reached),
                score=_num(run.score),
                decisions=run.decisions,
                forced=run.forced,
                invalid=run.invalid_decisions,
                tokens=f"{run.prompt_tokens:,}+{run.completion_tokens:,}",
                reward=_num(run.reward_total),
            )
        )
    return rows + [""]


def _outcome(run: RunMetrics) -> str:
    if not run.complete:
        return "CRASHED"
    return {True: "VICTORY", False: "DEFEAT"}.get(run.victory, "UNFINISHED")


def _floor_mark(run: RunMetrics) -> str:
    if run.floor_reached is None:
        return "?"
    return f"**{run.floor_reached} W**" if run.victory else str(run.floor_reached)


def _num(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(value)


def _latency(ms: float | None) -> str:
    return f"{ms / 1000:.1f}s" if ms is not None else "-"


def _cost(agg: SuiteAggregate, pricing: Pricing) -> str:
    rates = pricing.get(agg.key.model)
    if rates is None:
        return "-"
    rate_in, rate_out = rates
    costs = [
        (run.prompt_tokens * rate_in + run.completion_tokens * rate_out) / 1e6
        for run in agg.runs
    ]
    return f"${sum(costs) / len(costs):.2f}" if costs else "-"
