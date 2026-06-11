"""Env contract: what every backend (real game now, simulator later) provides.

The env speaks raw CommunicationMod dicts and command strings; typed
state/action layers sit above it (sts_bench.state, sts_bench.actions). The
protocol shape below is the stable surface -- it is also the future RL
interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# Raw CommunicationMod state message as parsed JSON.
RawState = dict[str, Any]


class EnvError(Exception):
    """The environment cannot continue (protocol violation, bad lifecycle call)."""


@dataclass(frozen=True)
class StepResult:
    """Outcome of one command: the next stable state, or a rejection.

    `error` is CommunicationMod's message when the command was refused; the
    game state is then unchanged. Rejections are normal control flow (they
    become corrective feedback for the model), never exceptions.
    """

    state: RawState
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Env(Protocol):
    def reset(self, character: str = "IRONCLAD", ascension: int = 0, seed: str | None = None) -> RawState: ...

    def step(self, command: str) -> StepResult: ...

    def legal_actions(self) -> list[str]: ...
