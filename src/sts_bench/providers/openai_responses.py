"""OpenAI Responses API provider: reasoning summaries and carried reasoning.

OpenAI never exposes reasoning on chat completions -- summaries of the hidden
trace exist only on the Responses API, which also keeps reasoning items alive
between tool rounds (on chat completions the model re-reasons from scratch
after every tool result). This provider speaks that wire format while keeping
the harness-facing contract identical: plain chat-format conversations in,
`ModelResponse` out.

The translation trick: the assistant message returned to the caller carries
the raw Responses output items under a private `_items` key. When the
conversation comes back for the next round, those items (reasoning included)
are echoed into `input` verbatim -- the documented stateless way to preserve
the model's train of thought -- and chat-style tool-result messages become
`function_call_output` items. The private key never goes over the wire.

OpenAI-only in practice: compat backends generally do not serve /responses.
"""

from __future__ import annotations

import os
from typing import Any

from .base import ModelResponse, ProviderError, Usage
from .openai_compat import OPENAI_BASE_URL, OpenAICompatProvider, _parse_tool_call

ITEMS_KEY = "_items"


class ResponsesProvider(OpenAICompatProvider):
    ENDPOINT = "/responses"

    def __init__(self, *args: Any, reasoning_summary: str | None = "auto", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._reasoning_summary = reasoning_summary

    @classmethod
    def from_env(
        cls,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> "ResponsesProvider":
        """Resolve an OpenAI backend; the chat resolver's Anthropic-first
        order would aim this wire format at servers that don't speak it."""
        base_url = base_url or os.environ.get("STS_BENCH_BASE_URL") or OPENAI_BASE_URL
        model = model or os.environ.get("STS_BENCH_MODEL")
        api_key = (
            api_key
            or os.environ.get("STS_BENCH_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise ProviderError(
                "the responses api needs an OPENAI_API_KEY (or STS_BENCH_API_KEY)"
            )
        if model is None:
            raise ProviderError(
                "no model configured for the responses api: pass --model or set STS_BENCH_MODEL"
            )
        return cls(base_url=base_url, model=model, api_key=api_key, **kwargs)

    def _payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        instructions: str | None = None
        items: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                instructions = msg.get("content")
            elif role == "user":
                items.append({"role": "user", "content": msg.get("content") or ""})
            elif role == "assistant":
                if ITEMS_KEY in msg:
                    items.extend(msg[ITEMS_KEY])
                elif msg.get("content"):  # foreign assistant message: keep the text
                    items.append({"role": "assistant", "content": msg["content"]})
            elif role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.get("tool_call_id") or "",
                        "output": msg.get("content") or "",
                    }
                )
        payload: dict[str, Any] = {"model": self.model, "input": items}
        if instructions is not None:
            payload["instructions"] = instructions
        if tools:
            # Responses declares functions flat, not nested under "function".
            payload["tools"] = [
                {"type": "function", **t["function"]} if "function" in t else t for t in tools
            ]
        reasoning: dict[str, str] = {}
        if self._reasoning_effort is not None:
            reasoning["effort"] = self._reasoning_effort
        if self._reasoning_summary is not None:
            reasoning["summary"] = self._reasoning_summary
        if reasoning:
            payload["reasoning"] = reasoning
        return payload

    def _parse(self, data: dict[str, Any]) -> ModelResponse:
        output = data.get("output")
        if not isinstance(output, list):
            raise ProviderError(f"malformed responses payload: {data}")

        texts: list[str] = []
        calls = []
        summaries: list[str] = []
        chat_tool_calls: list[dict[str, Any]] = []
        for item in output:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text" and part.get("text"):
                        texts.append(part["text"])
            elif kind == "function_call":
                wire = {
                    "id": item.get("call_id") or "",
                    "function": {"name": item.get("name"), "arguments": item.get("arguments")},
                }
                calls.append(_parse_tool_call(wire))
                chat_tool_calls.append({**wire, "type": "function"})
            elif kind == "reasoning":
                for part in item.get("summary") or []:
                    if part.get("text"):
                        summaries.append(part["text"])

        text = "\n".join(texts) or None
        reasoning = "\n\n".join(summaries) or None
        # Chat-shaped mirror of the turn, so transcripts, logging, and the
        # agent loop need no knowledge of the Responses item format.
        message: dict[str, Any] = {"role": "assistant", "content": text, ITEMS_KEY: output}
        if chat_tool_calls:
            message["tool_calls"] = chat_tool_calls
        if reasoning:
            message["reasoning_content"] = reasoning

        usage = data.get("usage") or {}
        details = usage.get("output_tokens_details") or {}
        return ModelResponse(
            message=message,
            text=text,
            tool_calls=tuple(calls),
            usage=Usage(
                prompt_tokens=usage.get("input_tokens") or 0,
                completion_tokens=usage.get("output_tokens") or 0,
                reasoning_tokens=details.get("reasoning_tokens") or 0,
            ),
            reasoning=reasoning,
        )
