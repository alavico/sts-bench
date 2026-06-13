"""Run metrics: the numbers the benchmark reports, computed from trajectories.

The trajectory JSONL is the source of truth -- every number here is
recomputable offline from the stored records alone, so a report never depends
on having watched the run live. One `RunMetrics` summarizes one run;
`aggregate` groups runs by configuration (model, scaffold, effort, and the
prompt/tool hashes that pin what the model actually saw) so comparisons only
average over runs that played the same benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median
from typing import Iterable

from ..replay import group
from ..trajectory import read_records


@dataclass(frozen=True)
class ConfigKey:
    """What must match for two runs to average together.

    The hashes ride along deliberately: a prompt or tool-schema change makes
    a new configuration even under the same model and scaffold, because the
    model saw a different benchmark.
    """

    model: str
    agent: str
    reasoning_effort: str | None
    character: str
    ascension: int
    prompt_hash: str | None
    tool_schema_hash: str | None

    @property
    def label(self) -> str:
        effort = f" effort={self.reasoning_effort}" if self.reasoning_effort else ""
        # Baselines record their own name as the model; "random / random" says
        # it once.
        scaffold = "" if self.agent == self.model else f" / {self.agent}"
        return f"{self.model}{scaffold}{effort}"


@dataclass
class RunMetrics:
    """One run, reduced to its reportable numbers."""

    run_id: str
    seed: str | None
    character: str
    ascension: int
    model: str
    agent: str
    api: str | None
    reasoning_effort: str | None
    prompt_hash: str | None
    tool_schema_hash: str | None
    # None victory: the run never reached game over (crash, abort, budget).
    victory: bool | None = None
    floor_reached: int | None = None
    score: int | None = None
    decisions: int = 0
    forced: int = 0
    invalid_actions: int = 0
    invalid_decisions: int = 0  # decisions with at least one rejected attempt
    unparsed_states: int = 0
    observation_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    latencies_ms: list[int] = field(default_factory=list)
    reward_total: float | None = None  # sum of floor rewards; None when no floor closed
    complete: bool = True  # a run record was written (the run did not die mid-flight)

    @property
    def config(self) -> ConfigKey:
        return ConfigKey(
            model=self.model,
            agent=self.agent,
            reasoning_effort=self.reasoning_effort,
            character=self.character,
            ascension=self.ascension,
            prompt_hash=self.prompt_hash,
            tool_schema_hash=self.tool_schema_hash,
        )

    @property
    def invalid_decision_rate(self) -> float:
        return self.invalid_decisions / self.decisions if self.decisions else 0.0

    @property
    def forced_rate(self) -> float:
        return self.forced / self.decisions if self.decisions else 0.0

    @property
    def observation_calls_per_decision(self) -> float:
        return self.observation_calls / self.decisions if self.decisions else 0.0

    @property
    def median_latency_ms(self) -> float | None:
        return median(self.latencies_ms) if self.latencies_ms else None

    @classmethod
    def from_records(cls, records: list) -> "RunMetrics":
        run, floors, decisions_by_floor = group(records)
        decisions = [d for floor in decisions_by_floor.values() for d in floor]

        if run is not None:
            metrics = cls(
                run_id=run.run_id,
                seed=run.seed,
                character=run.character,
                ascension=run.ascension,
                model=run.model,
                agent=run.agent,
                api=run.api,
                reasoning_effort=run.reasoning_effort,
                prompt_hash=run.prompt_hash,
                tool_schema_hash=run.tool_schema_hash,
                victory=run.outcome.victory,
                floor_reached=run.outcome.floor_reached,
                score=run.outcome.score,
                unparsed_states=run.totals.unparsed_states,
            )
        else:
            # The run died without its closing record; the floors and
            # decisions that landed still count, under an unknown identity.
            metrics = cls(
                run_id=floors[0].run_id if floors else (decisions[0].run_id if decisions else "unknown"),
                seed=None,
                character="?",
                ascension=0,
                model="?",
                agent="?",
                api=None,
                reasoning_effort=None,
                prompt_hash=None,
                tool_schema_hash=None,
                floor_reached=max((floor.floor for floor in floors), default=None),
                complete=False,
            )

        # Totals come from the decision records, not the run record's rollup:
        # the decision stream is the finer-grained truth, and it is present
        # even when the run record is not.
        for decision in decisions:
            metrics.decisions += 1
            if decision.forced_reason is not None:
                metrics.forced += 1
            metrics.invalid_actions += decision.invalid_actions
            if decision.invalid_actions:
                metrics.invalid_decisions += 1
            metrics.observation_calls += decision.observation_calls
            metrics.prompt_tokens += decision.usage.prompt_tokens
            metrics.completion_tokens += decision.usage.completion_tokens
            metrics.reasoning_tokens += decision.usage.reasoning_tokens
            metrics.cache_read_tokens += decision.usage.cache_read_tokens
            if decision.latency_ms is not None:
                metrics.latencies_ms.append(decision.latency_ms)

        rewards = [floor.reward.total for floor in floors if floor.reward is not None]
        if rewards:
            metrics.reward_total = sum(rewards)
        return metrics

    @classmethod
    def from_file(cls, path: Path) -> "RunMetrics":
        return cls.from_records(list(read_records(path)))


@dataclass
class SuiteAggregate:
    """One configuration's runs over a seed suite, ready for a report row."""

    key: ConfigKey
    runs: list[RunMetrics]

    @property
    def n(self) -> int:
        return len(self.runs)

    @property
    def wins(self) -> int:
        return sum(1 for run in self.runs if run.victory)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def mean_floor(self) -> float | None:
        floors = [run.floor_reached for run in self.runs if run.floor_reached is not None]
        return mean(floors) if floors else None

    @property
    def best_floor(self) -> int | None:
        floors = [run.floor_reached for run in self.runs if run.floor_reached is not None]
        return max(floors) if floors else None

    @property
    def mean_reward(self) -> float | None:
        rewards = [run.reward_total for run in self.runs if run.reward_total is not None]
        return mean(rewards) if rewards else None

    @property
    def decisions(self) -> int:
        return sum(run.decisions for run in self.runs)

    @property
    def invalid_decision_rate(self) -> float:
        invalid = sum(run.invalid_decisions for run in self.runs)
        return invalid / self.decisions if self.decisions else 0.0

    @property
    def forced_rate(self) -> float:
        forced = sum(run.forced for run in self.runs)
        return forced / self.decisions if self.decisions else 0.0

    @property
    def observation_calls_per_decision(self) -> float:
        calls = sum(run.observation_calls for run in self.runs)
        return calls / self.decisions if self.decisions else 0.0

    @property
    def median_latency_ms(self) -> float | None:
        latencies = [ms for run in self.runs for ms in run.latencies_ms]
        return median(latencies) if latencies else None

    @property
    def mean_tokens_per_run(self) -> tuple[float, float]:
        if not self.runs:
            return (0.0, 0.0)
        return (
            mean(run.prompt_tokens for run in self.runs),
            mean(run.completion_tokens for run in self.runs),
        )


def aggregate(runs: Iterable[RunMetrics]) -> list[SuiteAggregate]:
    """Group runs by configuration, in first-seen order."""
    groups: dict[ConfigKey, SuiteAggregate] = {}
    for run in runs:
        entry = groups.get(run.config)
        if entry is None:
            groups[run.config] = entry = SuiteAggregate(key=run.config, runs=[])
        entry.runs.append(run)
    return list(groups.values())


def hash_drift(aggregates: list[SuiteAggregate]) -> list[str]:
    """Configurations that look alike but saw different benchmarks.

    Two aggregates sharing (model, agent, effort) while differing in prompt or
    tool-schema hash are listed in separate rows of the report; these warnings
    say why the rows did not merge.
    """
    by_label: dict[str, list[ConfigKey]] = {}
    for agg in aggregates:
        by_label.setdefault(agg.key.label, []).append(agg.key)
    warnings = []
    for label, keys in by_label.items():
        if len(keys) > 1:
            warnings.append(
                f"{label}: {len(keys)} distinct prompt/tool-schema versions; "
                "rows are kept separate because the model saw different surfaces"
            )
    return warnings
