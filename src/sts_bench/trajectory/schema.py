"""Trajectory records: the structured, training-ready output of a run.

Records form a hierarchy mirroring the context design -- run > floor >
decision -- with each fact stored once, at the level that owns it:

- The **floor record** owns the floor's full conversation. One conversation
  per floor makes the floor's final request + final response a complete,
  self-contained packet; everything downstream (paired floor metrics, replay,
  SFT export) reads it whole.
- **Decision records** point into that conversation by message index range.
  They never copy messages -- a copy could drift from the packet, and a
  persistent conversation re-stored per decision grows quadratically.
- The **run record** owns config and totals, written once at the end.

All three kinds share one JSONL file per run, told apart by the `record`
discriminator. Every record carries `schema_version`, and floor rewards carry
their own `spec_version`, so files written today stay readable -- and their
rewards recomputable from the stored raw boundary states -- after either
evolves.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ..env.rewards import Reward
from ..providers.base import Usage

SCHEMA_VERSION = 1


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenUsage(Base):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Hidden-deliberation spend, already counted inside completion_tokens.
    reasoning_tokens: int = 0
    # Prompt tokens served from the provider's cache; stays 0 until the
    # providers report it, without a schema change when they do.
    cache_read_tokens: int = 0

    @classmethod
    def from_usage(cls, usage: Usage) -> "TokenUsage":
        return cls(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cache_read_tokens=usage.cache_read_tokens,
        )


class StateSummary(Base):
    """The scannable essentials of a boundary state; the raw state rides
    alongside for everything else."""

    floor: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    gold: int | None = None
    screen: str | None = None


class FloorScorecard(Base):
    """What the floor cost and yielded -- entry-conditioned, so floors are
    comparable across runs that arrive in different shape."""

    hp_delta: int = 0
    gold_delta: int = 0
    cards_gained: list[str] = Field(default_factory=list)
    relics_gained: list[str] = Field(default_factory=list)
    potions_gained: list[str] = Field(default_factory=list)
    combat_turns: int | None = None  # None: no combat on this floor


class RunOutcome(Base):
    # victory None: the run never reached a game-over screen (crash, abort,
    # step budget) -- distinct from a recorded defeat.
    victory: bool | None = None
    floor_reached: int | None = None
    score: int | None = None


class RunTotals(Base):
    decisions: int = 0
    forced: int = 0
    unparsed_states: int = 0
    usage: TokenUsage = Field(default_factory=TokenUsage)


class RunRecord(Base):
    record: Literal["run"] = "run"
    schema_version: int = SCHEMA_VERSION
    run_id: str
    started_at: str  # ISO 8601
    finished_at: str | None = None
    seed: str | None = None  # None: the game rolled its own
    character: str
    ascension: int = 0
    model: str
    provider_base_url: str
    api: str
    reasoning_effort: str | None = None
    agent: str
    # Hashes pin what the model actually saw: two runs compare cleanly only
    # if prompt and tool schemas match.
    prompt_hash: str | None = None
    tool_schema_hash: str | None = None
    outcome: RunOutcome = Field(default_factory=RunOutcome)
    totals: RunTotals = Field(default_factory=RunTotals)


class FloorRecord(Base):
    """The packet: one floor's complete story, self-contained."""

    record: Literal["floor"] = "floor"
    schema_version: int = SCHEMA_VERSION
    run_id: str
    floor: int
    floor_type: str | None = None  # room type as the wire names it (MonsterRoom, ...)
    # The floor's whole conversation in provider wire format (system prompt
    # included): final request messages plus the final response. Decision
    # records index into this list.
    conversation: list[dict[str, Any]] = Field(default_factory=list)
    entry: StateSummary = Field(default_factory=StateSummary)
    exit: StateSummary = Field(default_factory=StateSummary)
    # Raw game_state at the boundaries -- the ground truth rewards are
    # computed from, kept so a future reward spec can recompute.
    entry_state: dict[str, Any] = Field(default_factory=dict)
    exit_state: dict[str, Any] = Field(default_factory=dict)
    scorecard: FloorScorecard = Field(default_factory=FloorScorecard)
    reward: Reward | None = None


class DecisionRecord(Base):
    record: Literal["decision"] = "decision"
    schema_version: int = SCHEMA_VERSION
    run_id: str
    floor: int | None = None
    decision_index: int  # 1-based across the run, matching the protocol log
    # Half-open range [message_start, message_end) into the owning floor
    # record's conversation: the messages this decision added.
    message_start: int
    message_end: int
    screen: str | None = None
    action: str | None = None  # in the model's own terms; None when forced
    command: str | None = None  # what actually went over the wire
    forced_reason: str | None = None
    rounds: int = 0
    observation_calls: int = 0
    invalid_actions: int = 0
    latency_ms: int | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)

    @model_validator(mode="after")
    def _range_valid(self) -> "DecisionRecord":
        if self.message_start < 0 or self.message_end < self.message_start:
            raise ValueError(
                f"message range [{self.message_start}, {self.message_end}) is not a valid slice"
            )
        return self


TrajectoryRecord = Annotated[
    RunRecord | FloorRecord | DecisionRecord, Field(discriminator="record")
]

_adapter: TypeAdapter[TrajectoryRecord] = TypeAdapter(TrajectoryRecord)


def parse_record(data: str | bytes | dict[str, Any]) -> TrajectoryRecord:
    """One JSONL line (or already-decoded dict) back into its typed record."""
    if isinstance(data, (str, bytes)):
        return _adapter.validate_json(data)
    return _adapter.validate_python(data)
