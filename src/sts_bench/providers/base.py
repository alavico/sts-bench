"""Provider contract: chat completion with tool calling, nothing game-specific.

The provider knows nothing about Slay the Spire; the agent scaffold is the
only bridge. Messages travel as plain OpenAI-format dicts because an assistant
message containing tool calls must be echoed back into the history verbatim --
wrapping them in types would just mean unwrapping them again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(Exception):
    """The provider could not produce a response (after retries)."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation from the model.

    `arguments` is None when the model emitted arguments that were not valid
    JSON; `arguments_error` then says why, so the agent can feed it back as
    corrective feedback instead of crashing.
    """

    id: str
    name: str
    arguments: dict[str, Any] | None
    arguments_error: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    """One assistant turn.

    `message` is the raw assistant message dict, ready to append to the
    conversation history unchanged (required for tool-call follow-ups).
    """

    message: dict[str, Any]
    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)


class ModelProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse: ...
