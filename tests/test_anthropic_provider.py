"""Anthropic Messages provider: block translation in both directions. No network."""

import pytest

from sts_bench.providers import AnthropicProvider, ProviderError
from sts_bench.providers.anthropic_messages import BLOCKS_KEY, _AnthropicInputLimiter


def make_provider(transport, **kwargs):
    return AnthropicProvider(
        base_url="http://test.local/v1", model="claude-test", api_key="sk-test",
        transport=transport, **kwargs,
    )


def response(*content, usage=None):
    return {"content": list(content), "usage": usage or {}}


def text_block(text):
    return {"type": "text", "text": text}


def thinking_block(text):
    return {"type": "thinking", "thinking": text}


def tool_use_block(block_id, name, arguments):
    return {"type": "tool_use", "id": block_id, "name": name, "input": arguments}


def test_payload_translates_chat_conversation_to_native():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return response(text_block("ok"))

    chat_tools = [
        {"type": "function", "function": {"name": "play_card", "description": "play", "parameters": {"type": "object"}}}
    ]
    make_provider(transport).complete(
        [
            {"role": "system", "content": "you are playing"},
            {"role": "user", "content": "<run>floor 1</run>"},
        ],
        tools=chat_tools,
    )
    # system is a cache-marked block: tools+system is the run-stable prefix
    assert seen["system"] == [
        {"type": "text", "text": "you are playing", "cache_control": {"type": "ephemeral"}}
    ]
    assert seen["messages"] == [{"role": "user", "content": "<run>floor 1</run>"}]
    assert seen["tools"] == [
        {"name": "play_card", "description": "play", "input_schema": {"type": "object"}}
    ]
    assert seen["max_tokens"] > 0
    assert "thinking" not in seen  # off by default: zero-shot baseline untouched


def test_parses_text_thinking_and_structured_tool_input():
    provider = make_provider(
        lambda p: response(
            thinking_block("kill the Taskmaster before it ramps"),
            text_block("attacking."),
            tool_use_block("toolu_1", "play_card", {"card_index": 3, "target_index": 1}),
            usage={
                "input_tokens": 700,
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 300,
                "output_tokens": 120,
            },
        )
    )
    result = provider.complete([{"role": "user", "content": "state"}])
    assert result.reasoning == "kill the Taskmaster before it ramps"
    assert result.text == "attacking."
    (call,) = result.tool_calls
    # input arrives as structured JSON: no string parse, no arguments_error
    assert call.arguments == {"card_index": 3, "target_index": 1}
    assert call.arguments_error is None
    # prompt cost includes the cached portions
    assert result.usage.prompt_tokens == 3000
    assert result.usage.completion_tokens == 120
    # cache reads stay folded into prompt_tokens, and are also reported on their own
    assert result.usage.cache_read_tokens == 2000
    # chat-shaped mirror feeds the existing transcript/log machinery
    assert result.message["reasoning_content"] == result.reasoning
    assert result.message["tool_calls"][0]["function"]["name"] == "play_card"


def test_next_round_echoes_blocks_and_translates_tool_results():
    requests = []
    canned = [
        response(thinking_block("check the deck"), tool_use_block("toolu_1", "get_deck", {})),
        response(tool_use_block("toolu_2", "end_turn", {})),
    ]

    def transport(payload):
        requests.append(payload)
        return canned[len(requests) - 1]

    provider = make_provider(transport)
    conversation = [{"role": "user", "content": "state"}]
    first = provider.complete(conversation)
    conversation.append(first.message)  # exactly what the agent does
    conversation.append({"role": "tool", "tool_call_id": "toolu_1", "content": "10 cards"})
    provider.complete(conversation)

    second = requests[1]["messages"]
    # the assistant turn echoes the native blocks verbatim, thinking included
    assert second[1] == {
        "role": "assistant",
        "content": [thinking_block("check the deck"), tool_use_block("toolu_1", "get_deck", {})],
    }
    assert second[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "10 cards"}],
    }
    # and the private _blocks key never goes over the wire
    assert all(BLOCKS_KEY not in m for m in second)


def test_tool_result_and_followup_digest_share_one_user_turn():
    # A floor conversation closes the previous action's call and then sends a
    # fresh digest -- two chat messages that must land as one alternating user
    # turn, tool results first.
    seen = {}

    def transport(payload):
        seen.update(payload)
        return response(text_block("ok"))

    make_provider(transport).complete(
        [
            {"role": "user", "content": "state"},
            {"role": "assistant", "content": None, BLOCKS_KEY: [tool_use_block("toolu_1", "play_card", {})]},
            {"role": "tool", "tool_call_id": "toolu_1", "content": "executed"},
            {"role": "user", "content": "<run>floor 2</run>"},
        ]
    )
    assert [m["role"] for m in seen["messages"]] == ["user", "assistant", "user"]
    assert seen["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "executed"},
        {"type": "text", "text": "<run>floor 2</run>"},
    ]


def test_headers_use_anthropic_scheme():
    provider = make_provider(lambda p: response(text_block("ok")))
    headers = provider._headers()
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"]
    assert "Authorization" not in headers


def test_thinking_opt_in_requests_summarized_adaptive():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return response(text_block("ok"))

    make_provider(transport, thinking=True).complete([])
    assert seen["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_reasoning_effort_implies_thinking_and_sets_output_config():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return response(text_block("ok"))

    make_provider(transport, reasoning_effort="high").complete([])
    assert seen["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert seen["output_config"] == {"effort": "high"}


def test_sonnet_gets_default_input_token_limiter(monkeypatch):
    monkeypatch.delenv("STS_BENCH_ANTHROPIC_INPUT_TPM", raising=False)
    assert _AnthropicInputLimiter.from_env("claude-sonnet-4-6") is not None
    assert _AnthropicInputLimiter.from_env("claude-haiku-4-5") is None


def test_input_token_limiter_can_be_disabled(monkeypatch):
    monkeypatch.setenv("STS_BENCH_ANTHROPIC_INPUT_TPM", "0")
    assert _AnthropicInputLimiter.from_env("claude-sonnet-4-6") is None


def test_input_limiter_counts_cached_prefix_once():
    costs = []

    class Limiter:
        def acquire(self, estimated_tokens):
            costs.append(estimated_tokens)

    provider = make_provider(lambda p: response(text_block("ok")))
    provider._input_limiter = Limiter()
    provider.complete(
        [
            {"role": "system", "content": "stable system prompt" * 50},
            {"role": "user", "content": "state"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "stable tool schema" * 50,
                    "parameters": {"type": "object"},
                },
            }
        ],
    )
    provider.complete(
        [
            {"role": "system", "content": "stable system prompt" * 50},
            {"role": "user", "content": "state"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "stable tool schema" * 50,
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert len(costs) == 2
    assert costs[1] < costs[0]


def test_from_env_requires_a_key(monkeypatch):
    for var in ("STS_BENCH_API_KEY", "ANTHROPIC_API_KEY", "STS_BENCH_BASE_URL", "STS_BENCH_MODEL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider.from_env()
