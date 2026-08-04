"""The comparison report: one markdown table set over a set of runs.

Rows are configurations (model / scaffold / effort), never single runs --
runs whose prompt or tool-schema hashes differ stay in separate rows with a
warning saying why. Everything renders from `RunMetrics`, so a report is
recomputable from the trajectory files alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .metrics import ConfigAggregate, RunMetrics, aggregate, hash_drift

# $ per million tokens (input, output), keyed by exact model name. Cost is
# reported only for models listed here; everything else shows "-" rather
# than a stale guess.
Pricing = dict[str, tuple[float, float]]

# Cached prompt tokens bill at a fraction of the input rate, and that fraction
# is provider-specific. Anthropic and OpenAI credit a cache read at 0.1x the
# input rate (90% off); Gemini's implicit cache only discounts ~75%, so a hit
# still pays 0.25x -- charging it at 0.1x understated every Gemini run. cache_read
# tokens are a subset of prompt_tokens, so only the uncached remainder pays full
# input rate. Unknown backends default to the shallower 0.25x discount: a deeper
# discount is the exception, so this never surprises us by costing more than the
# estimate. Keyed by model-name prefix since pricing is already model-keyed.
DEFAULT_CACHE_READ_MULT = 0.25
_CACHE_READ_MULT = (
    ("claude-", 0.1),
    ("gpt-", 0.1),
    ("gemini-", 0.25),
)


def cache_read_mult(model: str) -> float:
    """The fraction of the input rate a cached prompt token bills at, by provider."""
    for prefix, mult in _CACHE_READ_MULT:
        if model.startswith(prefix):
            return mult
    return DEFAULT_CACHE_READ_MULT


def run_cost(
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int,
    rates: tuple[float, float],
    model: str,
) -> float:
    """Dollar cost of one run, crediting cached input at the model's cache rate."""
    rate_in, rate_out = rates
    uncached = prompt_tokens - cache_read_tokens
    return (
        uncached * rate_in
        + cache_read_tokens * rate_in * cache_read_mult(model)
        + completion_tokens * rate_out
    ) / 1e6


def comparison_report(
    runs: list[RunMetrics], *, pricing: Pricing | None = None
) -> str:
    aggregates = aggregate(runs)
    lines: list[str] = ["# Benchmark report", ""]
    for warning in hash_drift(aggregates):
        lines.append(f"> ⚠ {warning}")
    if hash_drift(aggregates):
        lines.append("")

    lines += _config_table(aggregates, pricing or {})
    lines += _floor_by_seed(aggregates)
    lines += _run_table(runs)
    return "\n".join(lines) + "\n"


def report_from_files(
    paths: Iterable[Path], *, pricing: Pricing | None = None
) -> str:
    """One report over any set of trajectory files, in the order given.

    Runs are sessions' artifacts, the report is a view: rerun it over any
    mix of trajectories -- baselines recorded last week, model runs from
    today -- and configurations aggregate exactly as if they had run in one
    session.
    """
    return comparison_report(
        [RunMetrics.from_file(Path(path)) for path in paths],
        pricing=pricing,
    )


def _config_table(aggregates: list[ConfigAggregate], pricing: Pricing) -> list[str]:
    header = (
        "| configuration | runs | wins | floor mean | floor median | floor best | reward mean "
        "| card skips | potions used | gold spent "
        "| invalid dec. | forced | lookups/dec | latency p50 | tokens/run (in+out) | cost/run |"
    )
    rows = [header, "|" + "---|" * 16]
    for agg in aggregates:
        prompt, completion = agg.mean_tokens_per_run
        rows.append(
            "| {label} | {n} | {wins} | {floor} | {median} | {best} | {reward} "
            "| {skips} | {potions} | {gold} | {invalid} "
            "| {forced} | {lookups} | {latency} | {tokens} | {cost} |".format(
                label=agg.key.label,
                n=agg.n,
                wins=agg.wins,
                floor=_num(agg.mean_floor),
                median=_num(agg.median_floor),
                best=_num(agg.best_floor),
                reward=_num(agg.mean_reward),
                skips=_pct(agg.skip_rate),
                potions=_pct(agg.potion_use_rate),
                gold=_pct(agg.gold_spent_ratio),
                invalid=f"{agg.invalid_decision_rate:.1%}",
                forced=f"{agg.forced_rate:.1%}",
                lookups=f"{agg.observation_calls_per_decision:.2f}",
                latency=_latency(agg.median_latency_ms),
                tokens=f"{prompt:,.0f}+{completion:,.0f}",
                cost=_cost(agg, pricing),
            )
        )
    return rows + [""]


def _floor_by_seed(aggregates: list[ConfigAggregate]) -> list[str]:
    """Floor reached, configuration x seed: the results at a glance.
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
        "| forced | invalid | tokens (in+out) | reward |",
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


def _pct(rate: float | None) -> str:
    return f"{rate:.0%}" if rate is not None else "-"


def _latency(ms: float | None) -> str:
    return f"{ms / 1000:.1f}s" if ms is not None else "-"


def _cost(agg: ConfigAggregate, pricing: Pricing) -> str:
    rates = pricing.get(agg.key.model)
    if rates is None:
        return "-"
    costs = [
        run_cost(run.prompt_tokens, run.completion_tokens, run.cache_read_tokens, rates, run.model)
        for run in agg.runs
    ]
    return f"${sum(costs) / len(costs):.2f}" if costs else "-"
