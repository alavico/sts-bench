"""Gemini Generate Content API provider: Gemini through its first-class surface.

Gemini's OpenAI-compat endpoint is a convenience shim that drops the feature we
care about most -- it returns no reasoning and rejects thinking config. The
native `POST /v1beta/models/{model}:generateContent` wire format is where the
thought summaries live: with `thinkingConfig.includeThoughts` the response
carries `{text, thought: true}` parts (a readable reasoning summary) alongside
the visible answer, and `usageMetadata.thoughtsTokenCount` reports thinking
spend exactly -- no `total - prompt - completion` heuristic.

Same translation trick as the Anthropic and Responses providers: the assistant
message returned to the caller carries the raw native `parts` under a private
`_parts` key, echoed back verbatim on the next round. This matters for tool
loops -- a function-call part carries a `thoughtSignature` that must be returned
unchanged to keep the model's reasoning continuous across the turn. The
chat-shaped mirror fields (`content`, `tool_calls`, `reasoning_content`) keep
the agent, transcripts, and logging working with no knowledge of the part format.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import ModelResponse, ProviderError, ToolCall, Usage
from .openai_compat import OpenAICompatProvider

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_MAX_TOKENS = 8192

PARTS_KEY = "_parts"

# Gemini's functionDeclaration schema is a restricted OpenAPI subset; standard
# JSON-Schema keywords outside it are rejected with HTTP 400 (e.g. a tool with
# `additionalProperties: false` -> "Unknown name additionalProperties"). Strip
# the ones our tools emit but Gemini doesn't accept, recursively.
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "default",
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "definitions",
        "title",
        "examples",
        "const",
        "patternProperties",
        "additionalItems",
    }
)


def _gemini_schema(node: Any) -> Any:
    """Drop JSON-Schema keywords Gemini's function declarations reject."""
    if isinstance(node, dict):
        return {
            k: _gemini_schema(v)
            for k, v in node.items()
            if k not in _UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(node, list):
        return [_gemini_schema(x) for x in node]
    return node


class GeminiProvider(OpenAICompatProvider):
    def __init__(
        self,
        *args: Any,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        include_thoughts: bool = True,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._max_tokens = max_tokens
        # Thought summaries are the whole reason this provider exists; on by
        # default. Thinking depth is left dynamic (no thinkingBudget) -- Gemini
        # has no medium/high effort knob, so reasoning_effort is not translated.
        self._include_thoughts = include_thoughts

    @classmethod
    def from_env(
        cls,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> "GeminiProvider":
        base_url = base_url or os.environ.get("STS_BENCH_BASE_URL") or GEMINI_BASE_URL
        model = model or os.environ.get("STS_BENCH_MODEL") or DEFAULT_GEMINI_MODEL
        api_key = (
            api_key
            or os.environ.get("STS_BENCH_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not api_key:
            raise ProviderError(
                "the gemini generate-content api needs a GOOGLE_API_KEY (or STS_BENCH_API_KEY)"
            )
        return cls(base_url=base_url, model=model, api_key=api_key, **kwargs)

    def _endpoint_url(self) -> str:
        # The model and method live in the path, not the body.
        return f"{self.base_url}/models/{self.model}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self._api_key or ""}

    def _payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        system: str | None = None
        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                system = msg.get("content")
            elif role == "user":
                contents.append(
                    {"role": "user", "parts": [{"text": msg.get("content") or ""}]}
                )
            elif role == "assistant":
                if PARTS_KEY in msg:
                    # Echo the native parts verbatim -- a functionCall part's
                    # thoughtSignature must survive unchanged for the model to
                    # keep its reasoning across the tool loop.
                    contents.append({"role": "model", "parts": msg[PARTS_KEY]})
                elif msg.get("content"):  # foreign assistant message: keep the text
                    contents.append({"role": "model", "parts": [{"text": msg["content"]}]})
            elif role == "tool":
                # The tool-call id is the function name (set in _parse), which is
                # what functionResponse matches on. One call per decision, so the
                # name is unambiguous within its turn.
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": msg.get("tool_call_id") or "",
                                    "response": {"result": msg.get("content") or ""},
                                }
                            }
                        ],
                    }
                )
        payload: dict[str, Any] = {
            "contents": _merge_user_runs(contents),
            "generationConfig": {
                "maxOutputTokens": self._max_tokens,
                "thinkingConfig": {"includeThoughts": self._include_thoughts},
            },
        }
        if system is not None:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": f["name"],
                            "description": f.get("description", ""),
                            "parameters": _gemini_schema(f["parameters"]),
                        }
                        for f in (t.get("function", t) for t in tools)
                    ]
                }
            ]
        return payload

    def _parse(self, data: dict[str, Any]) -> ModelResponse:
        candidates = data.get("candidates")
        if not candidates:
            raise ProviderError(f"no candidates in response: {data}")
        parts = candidates[0].get("content", {}).get("parts") or []

        texts: list[str] = []
        thoughts: list[str] = []
        calls: list[ToolCall] = []
        chat_tool_calls: list[dict[str, Any]] = []
        for part in parts:
            fc = part.get("functionCall")
            if fc is not None:
                name = fc.get("name") or ""
                arguments = fc.get("args")
                ok = isinstance(arguments, dict)
                # The function name is the tool-call id: functionResponse matches
                # on name, and the tool message echoes the id straight back.
                calls.append(
                    ToolCall(
                        id=name,
                        name=name,
                        arguments=arguments if ok else None,
                        arguments_error=None if ok else f"function args were not an object: {arguments!r}",
                    )
                )
                chat_tool_calls.append(
                    {
                        "id": name,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                )
            elif part.get("thought") and part.get("text"):
                thoughts.append(part["text"])
            elif part.get("text"):
                texts.append(part["text"])

        text = "\n".join(texts) or None
        reasoning = "\n\n".join(thoughts) or None
        message: dict[str, Any] = {"role": "assistant", "content": text, PARTS_KEY: parts}
        if chat_tool_calls:
            message["tool_calls"] = chat_tool_calls
        if reasoning:
            message["reasoning_content"] = reasoning

        usage = data.get("usageMetadata") or {}
        prompt_tokens = usage.get("promptTokenCount") or 0
        thoughts_tokens = usage.get("thoughtsTokenCount") or 0
        candidate_tokens = usage.get("candidatesTokenCount") or 0
        return ModelResponse(
            message=message,
            text=text,
            tool_calls=tuple(calls),
            usage=Usage(
                # candidatesTokenCount is the visible output; thoughts are billed
                # as output too, so fold them in. reasoning stays a subset.
                prompt_tokens=prompt_tokens,
                completion_tokens=candidate_tokens + thoughts_tokens,
                reasoning_tokens=thoughts_tokens,
                cache_read_tokens=usage.get("cachedContentTokenCount") or 0,
            ),
            reasoning=reasoning,
        )


def _merge_user_runs(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold consecutive user turns into one, parts in order.

    Generate Content wants user and model turns to alternate. Answering a tool
    call and then sending the next state digest arrives here as two user turns
    (functionResponse, then text) -- merge each run into a single user turn,
    order preserved.
    """
    merged: list[dict[str, Any]] = []
    for turn in contents:
        if merged and turn["role"] == "user" == merged[-1]["role"]:
            merged[-1] = {"role": "user", "parts": merged[-1]["parts"] + turn["parts"]}
        else:
            merged.append(turn)
    return merged
