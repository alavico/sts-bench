"""OpenAI-compat provider: payload shape, response parsing, retry behavior. No network."""

import pytest

from sts_bench.providers import OpenAICompatProvider, ProviderError
from sts_bench.providers.openai_compat import RetryableError


def completion(message, usage=None):
    return {"choices": [{"message": message}], "usage": usage or {}}


def make_provider(transport, **kwargs):
    return OpenAICompatProvider(
        base_url="http://test.local/v1", model="test-model", transport=transport, **kwargs
    )


def test_payload_carries_model_messages_and_tools():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return completion({"role": "assistant", "content": "hi"})

    provider = make_provider(transport)
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    provider.complete(messages, tools=tools)
    assert seen == {"model": "test-model", "messages": messages, "tools": tools}


def test_parses_text_response_and_usage():
    provider = make_provider(
        lambda p: completion(
            {"role": "assistant", "content": "thinking..."},
            usage={"prompt_tokens": 12, "completion_tokens": 5},
        )
    )
    response = provider.complete([])
    assert response.text == "thinking..."
    assert response.tool_calls == ()
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (12, 5)
    assert response.message["content"] == "thinking..."


def test_parses_tool_calls_with_json_arguments():
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "play_card", "arguments": '{"card_index": 0}'},
            }
        ],
    }
    response = make_provider(lambda p: completion(message)).complete([])
    (call,) = response.tool_calls
    assert (call.id, call.name, call.arguments) == ("call_1", "play_card", {"card_index": 0})
    assert call.arguments_error is None


def test_malformed_tool_arguments_surface_as_error_not_crash():
    message = {
        "role": "assistant",
        "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "choose", "arguments": "{oops"}}
        ],
    }
    (call,) = make_provider(lambda p: completion(message)).complete([]).tool_calls
    assert call.arguments is None
    assert "not valid JSON" in call.arguments_error


def test_retries_transient_failures_then_succeeds(monkeypatch):
    monkeypatch.setattr("sts_bench.providers.openai_compat.time.sleep", lambda s: None)
    attempts = []

    def flaky(payload):
        attempts.append(1)
        if len(attempts) < 3:
            raise RetryableError("HTTP 429: slow down")
        return completion({"role": "assistant", "content": "ok"})

    response = make_provider(flaky, max_retries=3).complete([])
    assert response.text == "ok"
    assert len(attempts) == 3


def test_gives_up_after_retry_budget(monkeypatch):
    monkeypatch.setattr("sts_bench.providers.openai_compat.time.sleep", lambda s: None)

    def always_down(payload):
        raise RetryableError("HTTP 503")

    with pytest.raises(ProviderError, match="after 3 attempts"):
        make_provider(always_down, max_retries=2).complete([])


def test_malformed_completion_raises():
    with pytest.raises(ProviderError, match="malformed"):
        make_provider(lambda p: {"choices": []}).complete([])


def test_from_env_explicit_config_wins(monkeypatch):
    for var in ("STS_BENCH_BASE_URL", "STS_BENCH_MODEL", "STS_BENCH_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("STS_BENCH_BASE_URL", "http://gpu-box:8000/v1")
    monkeypatch.setenv("STS_BENCH_MODEL", "qwen3")
    provider = OpenAICompatProvider.from_env()
    assert provider.base_url == "http://gpu-box:8000/v1"
    assert provider.model == "qwen3"


def test_from_env_falls_back_to_anthropic_key(monkeypatch):
    for var in ("STS_BENCH_BASE_URL", "STS_BENCH_MODEL", "STS_BENCH_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    provider = OpenAICompatProvider.from_env()
    assert provider.base_url == "https://api.anthropic.com/v1"
    assert provider.model  # a default model is chosen


def test_from_env_local_requires_model(monkeypatch):
    for var in ("STS_BENCH_BASE_URL", "STS_BENCH_MODEL", "STS_BENCH_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ProviderError, match="no model configured"):
        OpenAICompatProvider.from_env()
