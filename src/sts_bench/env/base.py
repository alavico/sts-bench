"""Env contract: what every backend (real game now, simulator later) provides.

For M1 the state is the raw CommunicationMod dict and actions are raw command
strings. M2 replaces both with typed schemas; the shape of the protocol below
is what stays stable -- it is also the future RL interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# Raw CommunicationMod state message until M2's typed schema lands.
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
