"""Shape a set of runs into the campaign report's data: plain dicts, ready to
embed as JSON.

The campaign page compares configurations the way the markdown report does --
same `RunMetrics`/`aggregate` numbers, same hash-drift discipline -- and adds
what tables can't carry: the floor-by-seed matrix, per-run floor series for
the HP overlay, and a link from every run down to its single-run report. The
floor series stays small (boundary numbers only, never conversations); the
raw model I/O lives in the linked per-run pages.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from ..replay import group
from ..runner.metrics import RunMetrics, SuiteAggregate, aggregate, hash_drift
from ..runner.reports import Pricing, run_cost
from ..runner.seeds import Suite
from ..trajectory import FloorRecord
from .page import render_html


@dataclass
class CampaignRun:
    """One run as the campaign page needs it: its metrics, a small floor
    series for charts, and where its full report lives."""

    metrics: RunMetrics
    floors: list[dict[str, Any]]
    start: dict[str, Any] | None  # state entering floor 1; the charts' origin
    page: str | None = None  # href of the single-run report, relative to the campaign page

    @classmethod
    def from_records(cls, records: list, page: str | None = None) -> "CampaignRun":
        _, floors, _ = group(records)
        return cls(
            metrics=RunMetrics.from_records(records),
            floors=[_floor_point(floor) for floor in floors],
            start=_start_point(floors),
            page=page,
        )


def _floor_point(floor: FloorRecord) -> dict[str, Any]:
    return {
        "floor": floor.floor,
        "act": floor.entry_state.get("act"),
        "type": floor.floor_type,
        "hp": floor.exit.hp,
        "max_hp": floor.exit.max_hp,
        "gold": floor.exit.gold,
    }


def _start_point(floors: list[FloorRecord]) -> dict[str, Any] | None:
    if not floors:
        return None
    first = floors[0]
    return {
        "floor": first.entry.floor,
        "hp": first.entry.hp,
        "max_hp": first.entry.max_hp,
        "gold": first.entry.gold,
    }


def build_campaign_data(
    runs: list[CampaignRun], *, suite: Suite | None = None, pricing: Pricing | None = None
) -> dict[str, Any]:
    aggregates = aggregate(run.metrics for run in runs)
    by_config = {agg.key: agg.key.label for agg in aggregates}
    return {
        "title": "Slay the Spire Bench",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "suite": (
            {
                "name": suite.name,
                "character": suite.character,
                "ascension": suite.ascension,
                "seeds": list(suite.seeds),
            }
            if suite
            else None
        ),
        "warnings": hash_drift(aggregates),
        "seeds": _seed_order(suite, runs),
        "configs": [_config(agg, pricing or {}) for agg in aggregates],
        "runs": [_run(run, by_config[run.metrics.config], pricing or {}) for run in runs],
    }


def render_campaign_html(data: dict[str, Any]) -> str:
    return render_html(
        data,
        template="campaign.html",
        css=("report.css", "campaign.css"),
        js=("campaign.js",),
        title=data["title"],
    )


def _seed_order(suite: Suite | None, runs: list[CampaignRun]) -> list[str]:
    """Suite order first, then any seed the suite didn't name, first seen."""
    seeds = list(suite.seeds) if suite else []
    for run in runs:
        seed = run.metrics.seed or "(random)"
        if seed not in seeds:
            seeds.append(seed)
    return seeds


def _config(agg: SuiteAggregate, pricing: Pricing) -> dict[str, Any]:
    prompt, completion = agg.mean_tokens_per_run
    return {
        "label": agg.key.label,
        "model": agg.key.model,
        "agent": agg.key.agent,
        "effort": agg.key.reasoning_effort,
        "character": agg.key.character,
        "ascension": agg.key.ascension,
        "prompt_hash": agg.key.prompt_hash,
        "tool_schema_hash": agg.key.tool_schema_hash,
        "n": agg.n,
        "wins": agg.wins,
        "mean_floor": agg.mean_floor,
        "median_floor": agg.median_floor,
        "best_floor": agg.best_floor,
        "skip_rate": agg.skip_rate,
        "potion_use_rate": agg.potion_use_rate,
        "gold_spent_ratio": agg.gold_spent_ratio,
        "invalid_rate": agg.invalid_decision_rate,
        "forced_rate": agg.forced_rate,
        "lookups_per_decision": agg.observation_calls_per_decision,
        "latency_p50_ms": agg.median_latency_ms,
        "mean_prompt_tokens": prompt,
        "mean_completion_tokens": completion,
        "cost_per_run": _cost_per_run(agg, pricing),
    }


def _run_cost(m: RunMetrics, pricing: Pricing) -> float | None:
    """Dollar cost of one run, or None when its model has no listed price."""
    rates = pricing.get(m.model)
    if rates is None:
        return None
    return run_cost(m.prompt_tokens, m.completion_tokens, m.cache_read_tokens, rates, m.model)


def _cost_per_run(agg: SuiteAggregate, pricing: Pricing) -> float | None:
    costs = [_run_cost(run, pricing) for run in agg.runs]
    costs = [c for c in costs if c is not None]
    return sum(costs) / len(costs) if costs else None


def _run(run: CampaignRun, config_label: str, pricing: Pricing) -> dict[str, Any]:
    m = run.metrics
    if not m.complete:
        outcome = "CRASHED"
    else:
        outcome = {True: "VICTORY", False: "DEFEAT", None: "UNFINISHED"}[m.victory]
    return {
        "run_id": m.run_id,
        "config": config_label,
        "seed": m.seed or "(random)",
        "outcome": outcome,
        "floor": m.floor_reached,
        "score": m.score,
        "decisions": m.decisions,
        "forced": m.forced,
        "invalid": m.invalid_decisions,
        "lookups": m.observation_calls,
        "skip_rate": m.skip_rate,
        "potions_used": m.potions_used,
        "potions_gained": m.potions_gained,
        "gold_final": m.gold_final,
        "prompt_tokens": m.prompt_tokens,
        "completion_tokens": m.completion_tokens,
        "cost": _run_cost(m, pricing),
        "page": run.page,
        "start": run.start,
        "floors": run.floors,
    }
