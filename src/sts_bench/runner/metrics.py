"""Run metrics: the numbers the benchmark reports, computed from trajectories.

The trajectory JSONL is the source of truth -- every number here is
recomputable offline from the stored records alone, so a report never depends
on having watched the run live. One `RunMetrics` summarizes one run;
`aggregate` groups runs by configuration (model, scaffold, effort, and the
prompt/tool hashes that pin what the model actually saw) so comparisons only
average over runs that played the same benchmark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median
from typing import Iterable

from ..replay import group
from ..trajectory import read_records

# Wire commands that decline a card reward (return_back translates to
# whichever of these the screen offers); "choose N" takes a card.
_SKIP_COMMANDS = ("skip", "return", "cancel", "leave")

# The action narration names the potion as it left its slot:
# "use_potion 2 (Fire Potion)".
_POTION_USE = re.compile(r"^use_potion \d+ \((.+)\)$")


def _potions_gained(floors: list, decisions: list) -> int:
    """Potions the run obtained: the scorecards' floor-boundary belt diffs,
    plus the starting belt, plus one for every drunk potion no recorded gain
    accounts for. A potion born and consumed within a single floor (Entropic
    Brew's products, a reward swigged mid-fight) never crosses a boundary,
    so the scorecards alone undercount -- but drinking it is proof enough
    that it was obtained, and the use rate must never exceed 100%."""
    gains: list[tuple[int, str]] = []
    if floors:
        for potion in floors[0].entry_state.get("potions") or []:
            name = str(potion.get("name") or "")
            if name and name != "Potion Slot":
                gains.append((0, name))
    for floor in floors:
        gains.extend((floor.floor, name) for name in floor.scorecard.potions_gained)
    total = len(gains)
    for decision in decisions:
        used = _POTION_USE.match(decision.action or "")
        if used is None:
            continue
        floor_no = decision.floor if decision.floor is not None else float("inf")
        hit = next((g for g in gains if g[1] == used.group(1) and g[0] <= floor_no), None)
        if hit is not None:
            gains.remove(hit)
        else:
            total += 1
    return total


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
        # The agent stays out of the label: it is part of a configuration's
        # identity, not its name. Reports show model and effort; how decisions
        # were scaffolded is the methodology's story.
        effort = f" effort={self.reasoning_effort}" if self.reasoning_effort else ""
        return f"{self.model}{effort}"


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
    # Strategic signals: how the run played, beyond how far it got. All from
    # records already stored -- skips and potion sips are decisions, gold
    # moves at floor boundaries.
    card_reward_decisions: int = 0
    card_skips: int = 0
    potions_gained: int = 0
    potions_used: int = 0
    gold_earned: int = 0
    gold_spent: int = 0
    gold_start: int = 0  # the purse at floor 1; spending it is not overspending
    gold_final: int | None = None
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

    @property
    def skip_rate(self) -> float | None:
        """Card rewards declined; never skipping is the deck-bloat signature."""
        if not self.card_reward_decisions:
            return None
        return self.card_skips / self.card_reward_decisions

    @property
    def potion_use_rate(self) -> float | None:
        """Potions drunk per potion picked up; hoarding shows as a low rate."""
        if not self.potions_gained:
            return None
        return self.potions_used / self.potions_gained

    @property
    def gold_spent_ratio(self) -> float | None:
        """Gold spent per gold available -- the starting purse plus all
        earnings; dying rich shows as a low ratio."""
        available = self.gold_start + self.gold_earned
        if not available:
            return None
        return self.gold_spent / available

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
            if decision.screen == "CARD_REWARD":
                metrics.card_reward_decisions += 1
                if (decision.command or "").startswith(_SKIP_COMMANDS):
                    metrics.card_skips += 1
            if (decision.action or "").startswith("use_potion"):
                metrics.potions_used += 1
            metrics.prompt_tokens += decision.usage.prompt_tokens
            metrics.completion_tokens += decision.usage.completion_tokens
            metrics.reasoning_tokens += decision.usage.reasoning_tokens
            metrics.cache_read_tokens += decision.usage.cache_read_tokens
            if decision.latency_ms is not None:
                metrics.latencies_ms.append(decision.latency_ms)

        metrics.potions_gained = _potions_gained(floors, decisions)
        for floor in floors:
            delta = floor.scorecard.gold_delta
            if delta > 0:
                metrics.gold_earned += delta
            else:
                metrics.gold_spent -= delta
        if floors:
            metrics.gold_start = floors[0].entry.gold or 0
            metrics.gold_final = floors[-1].exit.gold

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
    def median_floor(self) -> float | None:
        """Less skewed than the mean: one lucky seed can't drag it up."""
        floors = [run.floor_reached for run in self.runs if run.floor_reached is not None]
        return median(floors) if floors else None

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
    def skip_rate(self) -> float | None:
        rewards = sum(run.card_reward_decisions for run in self.runs)
        if not rewards:
            return None
        return sum(run.card_skips for run in self.runs) / rewards

    @property
    def potion_use_rate(self) -> float | None:
        gained = sum(run.potions_gained for run in self.runs)
        if not gained:
            return None
        return sum(run.potions_used for run in self.runs) / gained

    @property
    def gold_spent_ratio(self) -> float | None:
        available = sum(run.gold_start + run.gold_earned for run in self.runs)
        if not available:
            return None
        return sum(run.gold_spent for run in self.runs) / available

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
    """Identical setups whose prompt or tool schema was revised between runs.

    Most row splits need no comment: different ascension, character, or
    scaffold are simply different configurations. The one split worth a note
    is a prompt or tool-schema revision inside an otherwise identical setup --
    runs from while the scaffold was still being refined. Each version keeps
    its own row, so no number ever averages across revisions; the note records
    that provenance without casting doubt on the runs themselves.
    """
    setups: dict[tuple, list[ConfigKey]] = {}
    for agg in aggregates:
        k = agg.key
        setup = (k.model, k.agent, k.reasoning_effort, k.character, k.ascension)
        setups.setdefault(setup, []).append(k)
    warnings = []
    for keys in setups.values():
        versions = {(k.prompt_hash, k.tool_schema_hash) for k in keys}
        if len(versions) < 2:
            continue
        prompts = len({k.prompt_hash for k in keys}) > 1
        tools = len({k.tool_schema_hash for k in keys}) > 1
        what = "prompt" if prompts and not tools else "tool schema" if tools and not prompts else "prompt and tool schema"
        warnings.append(
            f"{keys[0].label}: the {what} was revised while the scaffold was being "
            f"refined ({len(versions)} versions); each version keeps its own row, "
            "so results are never averaged across revisions"
        )
    return warnings
