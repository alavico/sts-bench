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
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from .base import ModelResponse, ProviderError, ToolCall, Usage

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"

# An explicit base URL for a known vendor picks up that vendor's key from the
# environment -- no per-command STS_BENCH_API_KEY prefix needed. Matched by host
# substring against the base URL.
VENDOR_API_KEY_ENV: tuple[tuple[str, str], ...] = (
    ("api.anthropic.com", "ANTHROPIC_API_KEY"),
    ("api.openai.com", "OPENAI_API_KEY"),
    ("generativelanguage.googleapis.com", "GOOGLE_API_KEY"),
    ("api.moonshot.ai", "MOONSHOT_API_KEY"),
    ("api.z.ai", "ZAI_API_KEY"),
    ("openrouter.ai", "OPENROUTER_API_KEY"),
)

DEFAULT_TIMEOUT = 120.0
# Retries ride out transient provider errors (429 / 5xx / network). Demand
# spikes ("HTTP 503: high demand") can last minutes, so the budget is generous:
# 8 retries with exponential backoff capped at 60s/attempt waits ~3 min total
# before giving up. Override per run with STS_BENCH_MAX_RETRIES.
DEFAULT_MAX_RETRIES = 8
MAX_RETRY_WAIT = 60.0

# Visible-reasoning message keys used across OpenAI-compatible backends:
# `reasoning_content` (DeepSeek convention; vLLM, SGLang, LiteLLM) and
# `reasoning` (OpenRouter). OpenAI itself returns neither on chat completions.
REASONING_KEYS = frozenset({"reasoning_content", "reasoning"})

# transport: payload dict -> decoded response dict. Injectable for tests.
Transport = Callable[[dict[str, Any]], dict[str, Any]]


class RetryableError(Exception):
    """Transient failure (rate limit, 5xx, network); worth retrying."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class OpenAICompatProvider:
    ENDPOINT = "/chat/completions"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int | None = None,
        transport: Transport | None = None,
        reasoning_effort: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        # Explicit arg wins; otherwise the env override, otherwise the default.
        self._max_retries = (
            max_retries
            if max_retries is not None
            else _env_int("STS_BENCH_MAX_RETRIES", DEFAULT_MAX_RETRIES)
        )
        self._transport = transport or self._http_post
        # Reasoning models accept an effort level; left unset, gpt-5-class
        # models barely deliberate at all. Only sent when configured.
        self._reasoning_effort = reasoning_effort

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
        # `or None` folds an empty STS_BENCH_API_KEY (e.g. a `=$VENDOR_KEY`
        # prefix that expanded to nothing) back to None, so the vendor auto-pick
        # below still fires instead of sending an empty Authorization header.
        api_key = api_key or os.environ.get("STS_BENCH_API_KEY") or None

        if base_url is None:
            if api_key is None and os.environ.get("ANTHROPIC_API_KEY"):
                base_url, api_key = ANTHROPIC_BASE_URL, os.environ["ANTHROPIC_API_KEY"]
                model = model or "claude-haiku-4-5"
            elif api_key is None and os.environ.get("OPENAI_API_KEY"):
                base_url, api_key = OPENAI_BASE_URL, os.environ["OPENAI_API_KEY"]
                model = model or "gpt-4o-mini"
            else:
                base_url = OLLAMA_BASE_URL
        elif api_key is None:
            for host, env_var in VENDOR_API_KEY_ENV:
                if host in base_url:
                    api_key = os.environ.get(env_var)
                    break
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
        payload = self._payload(messages, tools)
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt == 0:
                self._before_request(payload)
            try:
                data = self._transport(payload)
                response = self._parse(data)
                self._after_success(payload, data)
                return response
            except RetryableError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    time.sleep(_retry_delay(exc, attempt))
        raise ProviderError(
            f"completion failed after {self._max_retries + 1} attempts: {last_error}"
        ) from last_error

    def _before_request(self, payload: dict[str, Any]) -> None:
        """Hook for provider-specific local rate protection."""

    def _after_success(self, payload: dict[str, Any], data: dict[str, Any]) -> None:
        """Hook for provider-specific local rate bookkeeping."""

    def _payload(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        # Reasoning traces stay in the local history for logging, but must not
        # go back over the wire: DeepSeek-convention backends reject inputs
        # carrying reasoning_content.
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_without_reasoning(m) for m in messages],
        }
        if tools:
            payload["tools"] = tools
        if self._reasoning_effort is not None:
            payload["reasoning_effort"] = self._reasoning_effort
        return payload

    def _parse(self, data: dict[str, Any]) -> ModelResponse:
        return _parse_response(data)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _endpoint_url(self) -> str:
        """Full request URL. Overridable for backends that route differently
        (e.g. Gemini puts the model and method in the path)."""
        return f"{self.base_url}{self.ENDPOINT}"

    def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._endpoint_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 429 or exc.code >= 500:
                raise RetryableError(
                    f"HTTP {exc.code}: {detail}", retry_after=_retry_after(exc.headers)
                ) from exc
            raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RetryableError(str(exc)) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"non-JSON response: {body[:500]!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.replace("_", ""))
    except ValueError as exc:
        raise ProviderError(f"{name} must be an integer") from exc


def _retry_delay(exc: RetryableError, attempt: int) -> float:
    if exc.retry_after is not None:
        return max(0.0, exc.retry_after)
    return min(2 ** attempt, MAX_RETRY_WAIT)


def _retry_after(headers: Any, now: float | None = None) -> float | None:
    """Best-effort retry delay from standard and Anthropic rate-limit headers."""

    now = time.time() if now is None else now
    retry_after = _header(headers, "retry-after")
    if retry_after:
        delay = _parse_delay_or_date(retry_after, now)
        if delay is not None:
            return delay

    reset_delays = [
        delay
        for name in (
            "anthropic-ratelimit-requests-reset",
            "anthropic-ratelimit-tokens-reset",
            "anthropic-ratelimit-input-tokens-reset",
            "anthropic-ratelimit-output-tokens-reset",
            "anthropic-priority-input-tokens-reset",
            "anthropic-priority-output-tokens-reset",
        )
        if (delay := _parse_reset_header(_header(headers, name), now)) is not None
    ]
    return max(reset_delays) if reset_delays else None


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        value = None
    if value is None and isinstance(headers, dict):
        value = next((v for k, v in headers.items() if k.lower() == name), None)
    return str(value).strip() if value is not None else None


def _parse_delay_or_date(value: str, now: float) -> float | None:
    try:
        return max(0.0, float(value))
    except ValueError:
        return _parse_reset_header(value, now)


def _parse_reset_header(value: str | None, now: float) -> float | None:
    if not value:
        return None
    text = value.strip()
    try:
        stamp = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return max(0.0, stamp.timestamp() - now)


def _without_reasoning(message: dict[str, Any]) -> dict[str, Any]:
    if REASONING_KEYS & message.keys():
        return {k: v for k, v in message.items() if k not in REASONING_KEYS}
    return message


def _parse_response(data: dict[str, Any]) -> ModelResponse:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"malformed completion response: {data}") from exc

    tool_calls = tuple(_parse_tool_call(tc) for tc in message.get("tool_calls") or [])
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    reasoning = next(
        (message[k] for k in REASONING_KEYS if isinstance(message.get(k), str) and message[k]),
        None,
    )
    prompt_tokens = usage.get("prompt_tokens") or 0
    completion_tokens = usage.get("completion_tokens") or 0
    reasoning_tokens = details.get("reasoning_tokens") or 0
    # Gemini's OpenAI-compat endpoint bills hidden thinking inside total_tokens
    # without folding it into completion_tokens or exposing a details breakdown.
    # Recover the gap so output (providers bill thinking as output) and the cost
    # report aren't undercounted. For backends where total == prompt + completion
    # this is a no-op; reasoning_tokens stays a subset of completion_tokens.
    total_tokens = usage.get("total_tokens") or 0
    hidden = total_tokens - prompt_tokens - completion_tokens
    if hidden > 0:
        completion_tokens += hidden
        reasoning_tokens = reasoning_tokens or hidden
    return ModelResponse(
        message=message,
        text=message.get("content"),
        tool_calls=tool_calls,
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=prompt_details.get("cached_tokens") or 0,
        ),
        reasoning=reasoning,
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
