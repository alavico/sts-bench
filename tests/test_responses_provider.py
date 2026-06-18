"""Responses API provider: item translation in both directions. No network."""

import pytest

from sts_bench.providers import ProviderError, ResponsesProvider


def make_provider(transport, **kwargs):
    return ResponsesProvider(
        base_url="http://test.local/v1", model="test-model", transport=transport, **kwargs
    )


def response(*output, usage=None):
    return {"output": list(output), "usage": usage or {}}


def reasoning_item(*texts):
    return {"type": "reasoning", "summary": [{"type": "summary_text", "text": t} for t in texts]}


def call_item(call_id, name, arguments):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


def message_item(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def test_payload_translates_chat_conversation_to_items():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return response(message_item("ok"))

    chat_tools = [{"type": "function", "function": {"name": "play_card", "parameters": {}}}]
    make_provider(transport).complete(
        [
            {"role": "system", "content": "you are playing"},
            {"role": "user", "content": "<run>floor 1</run>"},
        ],
        tools=chat_tools,
    )
    assert seen["instructions"] == "you are playing"
    assert seen["input"] == [{"role": "user", "content": "<run>floor 1</run>"}]
    # functions are declared flat on this wire, and summaries are requested
    assert seen["tools"] == [{"type": "function", "name": "play_card", "parameters": {}}]
    assert seen["reasoning"] == {"summary": "auto"}


def test_parses_reasoning_summary_calls_text_and_usage():
    provider = make_provider(
        lambda p: response(
            reasoning_item("low HP, kill the Taskmaster first"),
            call_item("c1", "play_card", '{"card_index": 3, "target_index": 1}'),
            usage={
                "input_tokens": 900,
                "output_tokens": 300,
                "output_tokens_details": {"reasoning_tokens": 280},
                "input_tokens_details": {"cached_tokens": 640},
            },
        )
    )
    result = provider.complete([{"role": "user", "content": "state"}])
    assert result.reasoning == "low HP, kill the Taskmaster first"
    (call,) = result.tool_calls
    assert (call.id, call.name, call.arguments) == ("c1", "play_card", {"card_index": 3, "target_index": 1})
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (900, 300)
    assert result.usage.reasoning_tokens == 280
    # cached prompt tokens are a sub-count of prompt_tokens, not added on top
    assert result.usage.cache_read_tokens == 640
    # the chat-shaped mirror feeds the existing transcript/log machinery
    assert result.message["reasoning_content"] == "low HP, kill the Taskmaster first"
    assert result.message["tool_calls"][0]["function"]["name"] == "play_card"


def test_next_round_echoes_items_and_translates_tool_results():
    requests = []
    canned = [
        response(reasoning_item("check the deck first"), call_item("c1", "get_deck", "{}")),
        response(call_item("c2", "end_turn", "{}")),
    ]

    def transport(payload):
        requests.append(payload)
        return canned[len(requests) - 1]

    provider = make_provider(transport)
    conversation = [{"role": "user", "content": "state"}]
    first = provider.complete(conversation)
    conversation.append(first.message)  # exactly what the agent does
    conversation.append({"role": "tool", "tool_call_id": "c1", "content": "10 cards"})
    provider.complete(conversation)

    second_input = requests[1]["input"]
    # the first turn's raw items (reasoning included) are echoed verbatim,
    # preserving the model's train of thought across tool rounds
    assert second_input[1] == reasoning_item("check the deck first")
    assert second_input[2] == call_item("c1", "get_deck", "{}")
    assert second_input[3] == {"type": "function_call_output", "call_id": "c1", "output": "10 cards"}
    # and the private _items key never goes over the wire
    assert all("_items" not in item for item in second_input if isinstance(item, dict))


def test_malformed_arguments_surface_as_feedback_not_crash():
    provider = make_provider(lambda p: response(call_item("c1", "play_card", "not json")))
    (call,) = provider.complete([]).tool_calls
    assert call.arguments is None
    assert "not valid JSON" in call.arguments_error


def test_from_env_targets_openai_even_with_an_anthropic_key(monkeypatch):
    for var in ("STS_BENCH_BASE_URL", "STS_BENCH_MODEL", "STS_BENCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    provider = ResponsesProvider.from_env(model="gpt-test")
    assert "api.openai.com" in provider.base_url
    assert provider._api_key == "sk-openai-test"

    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        ResponsesProvider.from_env(model="gpt-test")


def test_from_env_requires_a_model(monkeypatch):
    for var in ("STS_BENCH_BASE_URL", "STS_BENCH_MODEL", "STS_BENCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    with pytest.raises(ProviderError, match="--model"):
        ResponsesProvider.from_env()


def test_reasoning_effort_joins_the_summary_request():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return response(message_item("ok"))

    make_provider(transport, reasoning_effort="medium").complete([])
    assert seen["reasoning"] == {"effort": "medium", "summary": "auto"}


def test_reasoning_summary_can_be_disabled():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return response(message_item("ok"))

    make_provider(transport, reasoning_summary=None).complete([])
    assert "reasoning" not in seen
