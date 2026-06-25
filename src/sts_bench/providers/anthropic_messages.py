"""Anthropic Messages API provider: Claude through its first-class surface.

Anthropic's OpenAI-compat endpoint is a quick-evaluation shim; the native
`POST /v1/messages` wire format is where Claude's actual feature set lives:
thinking blocks (visible reasoning summaries), prompt caching (the stable
tools+system prefix of every decision bills at cache-read rates), and tool
inputs delivered as structured JSON rather than a string to re-parse -- the
malformed-arguments failure class cannot happen here.

Same translation trick as the Responses provider: the assistant message
returned to the caller carries the raw native content blocks under a private
`_blocks` key, echoed back verbatim on the next round (thinking blocks must
be passed back unchanged when continuing a tool loop). The chat-shaped
mirror fields (`content`, `tool_calls`, `reasoning_content`) keep the agent,
transcripts, and logging working with zero knowledge of the block format.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from .base import ModelResponse, ProviderError, ToolCall, Usage
from .openai_compat import ANTHROPIC_BASE_URL, OpenAICompatProvider

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_SONNET_INPUT_TPM = 30_000
CHARS_PER_TOKEN = 4

BLOCKS_KEY = "_blocks"


class AnthropicProvider(OpenAICompatProvider):
    ENDPOINT = "/messages"  # base_url already ends in /v1

    def __init__(
        self,
        *args: Any,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        thinking: bool = False,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._max_tokens = max_tokens
        # Adaptive thinking with summarized display (Claude 4.6+ models);
        # off by default to keep the zero-shot baseline untouched. A
        # reasoning_effort (low/medium/high/max here) implies thinking on.
        self._thinking = thinking or self._reasoning_effort is not None
        self._cache_warmed = False
        self._input_limiter = _AnthropicInputLimiter.from_env(self.model)

    @classmethod
    def from_env(
        cls,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> "AnthropicProvider":
        base_url = base_url or os.environ.get("STS_BENCH_BASE_URL") or ANTHROPIC_BASE_URL
        model = model or os.environ.get("STS_BENCH_MODEL") or DEFAULT_ANTHROPIC_MODEL
        api_key = (
            api_key
            or os.environ.get("STS_BENCH_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        if not api_key:
            raise ProviderError(
                "the native messages api needs an ANTHROPIC_API_KEY (or STS_BENCH_API_KEY)"
            )
        return cls(base_url=base_url, model=model, api_key=api_key, **kwargs)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def _payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        system: str | None = None
        native: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                system = msg.get("content")
            elif role == "user":
                native.append({"role": "user", "content": msg.get("content") or ""})
            elif role == "assistant":
                if BLOCKS_KEY in msg:
                    native.append({"role": "assistant", "content": msg[BLOCKS_KEY]})
                elif msg.get("content"):  # foreign assistant message: keep the text
                    native.append({"role": "assistant", "content": msg["content"]})
            elif role == "tool":
                native.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id") or "",
                                "content": msg.get("content") or "",
                            }
                        ],
                    }
                )
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "messages": _merge_user_runs(native),
        }
        if system is not None:
            # Breakpoint on the system block caches tools+system -- the part
            # that is byte-identical across every round of every decision.
            payload["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if tools:
            payload["tools"] = [
                {
                    "name": f["name"],
                    "description": f.get("description", ""),
                    "input_schema": f["parameters"],
                }
                for f in (t.get("function", t) for t in tools)
            ]
        if self._thinking:
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self._reasoning_effort is not None:
            payload["output_config"] = {"effort": self._reasoning_effort}
        return payload

    def _before_request(self, payload: dict[str, Any]) -> None:
        if self._input_limiter is not None:
            self._input_limiter.acquire(
                _estimate_uncached_input_tokens(payload, self._cache_warmed)
            )

    def _after_success(self, payload: dict[str, Any], data: dict[str, Any]) -> None:
        self._cache_warmed = True

    def _parse(self, data: dict[str, Any]) -> ModelResponse:
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderError(f"malformed messages payload: {data}")

        texts: list[str] = []
        thoughts: list[str] = []
        calls: list[ToolCall] = []
        chat_tool_calls: list[dict[str, Any]] = []
        for block in content:
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                texts.append(block["text"])
            elif kind == "thinking" and block.get("thinking"):
                thoughts.append(block["thinking"])
            elif kind == "tool_use":
                arguments = block.get("input")
                ok = isinstance(arguments, dict)
                calls.append(
                    ToolCall(
                        id=block.get("id") or "",
                        name=block.get("name") or "",
                        arguments=arguments if ok else None,
                        arguments_error=None if ok else f"tool input was not an object: {arguments!r}",
                    )
                )
                chat_tool_calls.append(
                    {
                        "id": block.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": block.get("name") or "",
                            "arguments": json.dumps(arguments),
                        },
                    }
                )

        text = "\n".join(texts) or None
        reasoning = "\n\n".join(thoughts) or None
        message: dict[str, Any] = {"role": "assistant", "content": text, BLOCKS_KEY: content}
        if chat_tool_calls:
            message["tool_calls"] = chat_tool_calls
        if reasoning:
            message["reasoning_content"] = reasoning

        usage = data.get("usage") or {}
        cache_read_tokens = usage.get("cache_read_input_tokens") or 0
        prompt_tokens = (
            (usage.get("input_tokens") or 0)
            + cache_read_tokens
            + (usage.get("cache_creation_input_tokens") or 0)
        )
        return ModelResponse(
            message=message,
            text=text,
            tool_calls=tuple(calls),
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=usage.get("output_tokens") or 0,
                cache_read_tokens=cache_read_tokens,
            ),
            reasoning=reasoning,
        )


def _merge_user_runs(native: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine consecutive user-role messages into single alternating turns.

    The Messages API wants user and assistant turns to alternate, and tool
    results to open the user turn they belong to. A conversation that answers
    several tool calls, or follows a tool result with a fresh state digest,
    arrives here as a run of user-role messages -- fold each run into one
    message, order preserved (tool results were emitted first, so they stay
    in front of any text).
    """
    merged: list[dict[str, Any]] = []
    for msg in native:
        if merged and msg["role"] == "user" == merged[-1]["role"]:
            merged[-1] = {
                "role": "user",
                "content": _blocks(merged[-1]["content"]) + _blocks(msg["content"]),
            }
        else:
            merged.append(msg)
    return merged


def _blocks(content: Any) -> list[dict[str, Any]]:
    return content if isinstance(content, list) else [{"type": "text", "text": content}]


class _AnthropicInputLimiter:
    """Process-local token bucket for Anthropic ITPM protection.

    Anthropic computes exact input token usage server-side. This client-side
    bucket uses a conservative character estimate to avoid preventable local
    bursts, while 429 handling still obeys the authoritative response headers.
    """

    def __init__(self, tokens_per_minute: int):
        self._capacity = float(tokens_per_minute)
        self._fill_rate = self._capacity / 60.0
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, model: str) -> "_AnthropicInputLimiter | None":
        raw = os.environ.get("STS_BENCH_ANTHROPIC_INPUT_TPM")
        if raw is None:
            limit = DEFAULT_SONNET_INPUT_TPM if "sonnet" in model.lower() else 0
        else:
            try:
                limit = int(raw.replace("_", ""))
            except ValueError as exc:
                raise ProviderError(
                    "STS_BENCH_ANTHROPIC_INPUT_TPM must be an integer token-per-minute limit"
                ) from exc
        return cls(limit) if limit > 0 else None

    def acquire(self, estimated_tokens: int) -> None:
        cost = min(float(max(estimated_tokens, 1)), self._capacity)
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated
                self._tokens = min(self._capacity, self._tokens + elapsed * self._fill_rate)
                self._updated = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                wait = (cost - self._tokens) / self._fill_rate
            time.sleep(wait)


def _estimate_uncached_input_tokens(
    payload: dict[str, Any], cache_warmed: bool
) -> int:
    counted = {"messages": payload.get("messages", [])}
    if not cache_warmed:
        if "system" in payload:
            counted["system"] = payload["system"]
        if "tools" in payload:
            counted["tools"] = payload["tools"]
    text = json.dumps(counted, ensure_ascii=False, separators=(",", ":"))
    return max(1, len(text) // CHARS_PER_TOKEN)
