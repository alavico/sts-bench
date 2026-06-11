"""Chat-completions provider over the OpenAI wire format, stdlib HTTP only.

One wire format covers most serving stacks: OpenAI itself, Anthropic's
compatibility endpoint, vLLM, SGLang, Ollama, Together, and friends all speak
`POST {base_url}/chat/completions`. The base URL, model, and key select the
backend; nothing else changes.

No SDK dependency on purpose -- a non-streaming chat completion is one JSON
POST, and urllib is enough.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .base import ModelResponse, ProviderError, ToolCall, Usage

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"

DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 3

# transport: payload dict -> decoded response dict. Injectable for tests.
Transport = Callable[[dict[str, Any]], dict[str, Any]]


class RetryableError(Exception):
    """Transient failure (rate limit, 5xx, network); worth retrying."""


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport or self._http_post

    @classmethod
    def from_env(
        cls,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> "OpenAICompatProvider":
        """Resolve backend from env vars; explicit arguments win.

        STS_BENCH_BASE_URL / STS_BENCH_MODEL / STS_BENCH_API_KEY are checked
        first. Without them: an ANTHROPIC_API_KEY or OPENAI_API_KEY selects
        that vendor's endpoint with a cheap default model; otherwise a local
        Ollama server is assumed and the model must be named explicitly.
        """
        base_url = base_url or os.environ.get("STS_BENCH_BASE_URL")
        model = model or os.environ.get("STS_BENCH_MODEL")
        api_key = api_key or os.environ.get("STS_BENCH_API_KEY")

        if base_url is None:
            if api_key is None and os.environ.get("ANTHROPIC_API_KEY"):
                base_url, api_key = ANTHROPIC_BASE_URL, os.environ["ANTHROPIC_API_KEY"]
                model = model or "claude-haiku-4-5"
            elif api_key is None and os.environ.get("OPENAI_API_KEY"):
                base_url, api_key = OPENAI_BASE_URL, os.environ["OPENAI_API_KEY"]
                model = model or "gpt-4o-mini"
            else:
                base_url = OLLAMA_BASE_URL
        if model is None:
            raise ProviderError(
                f"no model configured for {base_url}: pass --model or set STS_BENCH_MODEL"
            )
        return cls(base_url=base_url, model=model, api_key=api_key, **kwargs)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                time.sleep(min(2 ** (attempt - 1), 30))
            try:
                return _parse_response(self._transport(payload))
            except RetryableError as exc:
                last_error = exc
        raise ProviderError(
            f"chat completion failed after {self._max_retries + 1} attempts: {last_error}"
        ) from last_error

    def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 429 or exc.code >= 500:
                raise RetryableError(f"HTTP {exc.code}: {detail}") from exc
            raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RetryableError(str(exc)) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"non-JSON response: {body[:500]!r}") from exc


def _parse_response(data: dict[str, Any]) -> ModelResponse:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"malformed completion response: {data}") from exc

    tool_calls = tuple(_parse_tool_call(tc) for tc in message.get("tool_calls") or [])
    usage = data.get("usage") or {}
    return ModelResponse(
        message=message,
        text=message.get("content"),
        tool_calls=tool_calls,
        usage=Usage(
            prompt_tokens=usage.get("prompt_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or 0,
        ),
    )


def _parse_tool_call(tc: dict[str, Any]) -> ToolCall:
    function = tc.get("function") or {}
    raw_args = function.get("arguments") or "{}"
    arguments: dict[str, Any] | None
    arguments_error: str | None = None
    try:
        arguments = json.loads(raw_args)
        if not isinstance(arguments, dict):
            arguments, arguments_error = None, f"arguments must be a JSON object, got: {raw_args[:200]}"
    except json.JSONDecodeError as exc:
        arguments, arguments_error = None, f"arguments were not valid JSON ({exc}): {raw_args[:200]}"
    return ToolCall(
        id=tc.get("id") or "",
        name=function.get("name") or "",
        arguments=arguments,
        arguments_error=arguments_error,
    )
