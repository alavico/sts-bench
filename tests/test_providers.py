"""OpenAI-compat provider: payload shape, response parsing, retry behavior. No network."""

import pytest

from sts_bench.providers import OpenAICompatProvider, ProviderError
from sts_bench.providers.openai_compat import RetryableError, _retry_after


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


def test_from_env_explicit_known_base_url_picks_that_vendors_key(monkeypatch):
    from sts_bench.providers import OpenAICompatProvider

    for var in ("STS_BENCH_BASE_URL", "STS_BENCH_MODEL", "STS_BENCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    provider = OpenAICompatProvider.from_env(
        model="gpt-test", base_url="https://api.openai.com/v1"
    )
    assert provider._api_key == "sk-openai-test"


def test_reasoning_effort_is_sent_only_when_configured():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return completion({"role": "assistant", "content": "ok"})

    make_provider(transport).complete([])
    assert "reasoning_effort" not in seen
    make_provider(transport, reasoning_effort="high").complete([])
    assert seen["reasoning_effort"] == "high"


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


def test_reasoning_token_count_is_parsed():
    provider = make_provider(
        lambda p: completion(
            {"role": "assistant", "content": "end."},
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 250,
                "completion_tokens_details": {"reasoning_tokens": 240},
            },
        )
    )
    usage = provider.complete([]).usage
    assert usage.reasoning_tokens == 240
    assert usage.completion_tokens == 250  # reasoning is a subset, not extra
    total = usage + usage
    assert total.reasoning_tokens == 480


def test_reasoning_content_is_captured_and_kept_in_message():
    message = {"role": "assistant", "content": "strike.", "reasoning_content": "low HP, kill fastest"}
    response = make_provider(lambda p: completion(message)).complete([])
    assert response.reasoning == "low HP, kill fastest"
    assert response.text == "strike."
    # raw message keeps the trace so the transcript (and its log) can show it
    assert response.message["reasoning_content"] == "low HP, kill fastest"


def test_no_reasoning_means_none():
    response = make_provider(lambda p: completion({"role": "assistant", "content": "hi"})).complete([])
    assert response.reasoning is None


def test_reasoning_is_stripped_from_outbound_messages():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return completion({"role": "assistant", "content": "ok"})

    history = [
        {"role": "user", "content": "state"},
        {"role": "assistant", "content": "strike.", "reasoning_content": "because..."},
        {"role": "user", "content": "next state"},
    ]
    make_provider(transport).complete(history)
    assert seen["messages"][1] == {"role": "assistant", "content": "strike."}
    # the local history object is untouched, only the wire copy is cleaned
    assert history[1]["reasoning_content"] == "because..."


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
    sleeps = []
    monkeypatch.setattr("sts_bench.providers.openai_compat.time.sleep", sleeps.append)
    attempts = []

    def flaky(payload):
        attempts.append(1)
        if len(attempts) < 3:
            raise RetryableError("HTTP 429: slow down")
        return completion({"role": "assistant", "content": "ok"})

    response = make_provider(flaky, max_retries=3).complete([])
    assert response.text == "ok"
    assert len(attempts) == 3
    assert sleeps == [1, 2]


def test_retryable_error_retry_after_controls_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr("sts_bench.providers.openai_compat.time.sleep", sleeps.append)
    attempts = []

    def flaky(payload):
        attempts.append(1)
        if len(attempts) == 1:
            raise RetryableError("HTTP 429: slow down", retry_after=12.5)
        return completion({"role": "assistant", "content": "ok"})

    response = make_provider(flaky, max_retries=1).complete([])
    assert response.text == "ok"
    assert sleeps == [12.5]


def test_retry_after_parses_anthropic_reset_headers():
    headers = {"anthropic-ratelimit-input-tokens-reset": "2026-06-25T12:00:05Z"}
    assert _retry_after(headers, now=1_782_388_800.0) == 5.0


def test_retry_after_header_wins_over_reset_headers():
    headers = {
        "retry-after": "3",
        "anthropic-ratelimit-input-tokens-reset": "2026-06-25T12:00:30Z",
    }
    assert _retry_after(headers, now=1_782_388_800.0) == 3


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
