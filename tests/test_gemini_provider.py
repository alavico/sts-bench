"""Gemini native provider: generateContent payload shape, thought parsing,
tool-call echo, usage. No network."""

from sts_bench.providers.gemini_native import (
    GEMINI_BASE_URL,
    PARTS_KEY,
    GeminiProvider,
)


def make_provider(transport, **kwargs):
    return GeminiProvider(
        base_url="https://gen.test/v1beta",
        model="gemini-3.5-flash",
        api_key="k",
        transport=transport,
        **kwargs,
    )


def gen_response(parts, usage=None):
    return {"candidates": [{"content": {"parts": parts}}], "usageMetadata": usage or {}}


def function_call_part(name, args, signature="sig"):
    return {"functionCall": {"name": name, "args": args, "id": "g123"}, "thoughtSignature": signature}


def test_payload_translates_chat_to_generate_content():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return gen_response([{"text": "ok"}])

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
    assert seen["systemInstruction"] == {"parts": [{"text": "you are playing"}]}
    assert seen["contents"] == [{"role": "user", "parts": [{"text": "<run>floor 1</run>"}]}]
    assert seen["tools"] == [
        {"functionDeclarations": [{"name": "play_card", "description": "play", "parameters": {"type": "object"}}]}
    ]
    # thought summaries are requested; that is the whole point of going native
    assert seen["generationConfig"]["thinkingConfig"] == {"includeThoughts": True}
    assert seen["generationConfig"]["maxOutputTokens"] > 0


def test_parses_thoughts_text_and_function_call():
    provider = make_provider(
        lambda p: gen_response(
            [
                {"text": "kill the Taskmaster before it ramps", "thought": True},
                {"text": "attacking."},
                function_call_part("play_card", {"card_index": 3, "target_index": 1}),
            ],
            usage={
                "promptTokenCount": 700,
                "candidatesTokenCount": 20,
                "thoughtsTokenCount": 120,
                "cachedContentTokenCount": 200,
                "totalTokenCount": 840,
            },
        )
    )
    result = provider.complete([{"role": "user", "content": "state"}])
    # thought-summary part surfaces as visible reasoning -- the native win
    assert result.reasoning == "kill the Taskmaster before it ramps"
    assert result.text == "attacking."
    (call,) = result.tool_calls
    assert call.arguments == {"card_index": 3, "target_index": 1}
    assert call.arguments_error is None
    assert (call.id, call.name) == ("play_card", "play_card")  # name doubles as id
    # thoughts are billed as output, folded into completion; reasoning is a subset
    assert result.usage.completion_tokens == 140
    assert result.usage.reasoning_tokens == 120
    assert result.usage.prompt_tokens == 700
    assert result.usage.cache_read_tokens == 200
    # chat-shaped mirror feeds the existing transcript/log machinery
    assert result.message["reasoning_content"] == result.reasoning
    assert result.message["tool_calls"][0]["function"]["name"] == "play_card"


def test_malformed_function_args_surface_as_error_not_crash():
    (call,) = make_provider(
        lambda p: gen_response([{"functionCall": {"name": "choose", "args": "oops"}}])
    ).complete([]).tool_calls
    assert call.arguments is None
    assert "not an object" in call.arguments_error


def test_next_round_echoes_parts_and_translates_tool_result():
    requests = []
    canned = [
        gen_response([function_call_part("get_deck", {}, signature="deck-sig")]),
        gen_response([function_call_part("end_turn", {})]),
    ]

    def transport(payload):
        requests.append(payload)
        return canned[len(requests) - 1]

    provider = make_provider(transport)
    conversation = [{"role": "user", "content": "state"}]
    first = provider.complete(conversation)
    conversation.append(first.message)  # exactly what the agent does
    conversation.append({"role": "tool", "tool_call_id": "get_deck", "content": "10 cards"})
    provider.complete(conversation)

    contents = requests[1]["contents"]
    # the model turn echoes the native parts verbatim -- thoughtSignature intact
    assert contents[1] == {
        "role": "model",
        "parts": [function_call_part("get_deck", {}, signature="deck-sig")],
    }
    # the tool result becomes a functionResponse matched by name
    assert contents[2] == {
        "role": "user",
        "parts": [{"functionResponse": {"name": "get_deck", "response": {"result": "10 cards"}}}],
    }
    # the private parts key never goes over the wire as a top-level field
    assert all(PARTS_KEY not in turn for turn in contents)


def test_tool_result_and_followup_digest_share_one_user_turn():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return gen_response([{"text": "ok"}])

    make_provider(transport).complete(
        [
            {"role": "user", "content": "state"},
            {"role": "assistant", "content": None, PARTS_KEY: [function_call_part("play_card", {})]},
            {"role": "tool", "tool_call_id": "play_card", "content": "executed"},
            {"role": "user", "content": "<run>floor 2</run>"},
        ]
    )
    assert [c["role"] for c in seen["contents"]] == ["user", "model", "user"]
    # functionResponse and the next digest fold into one alternating user turn
    assert seen["contents"][2]["parts"] == [
        {"functionResponse": {"name": "play_card", "response": {"result": "executed"}}},
        {"text": "<run>floor 2</run>"},
    ]


def test_tool_schema_strips_keywords_gemini_rejects():
    seen = {}

    def transport(payload):
        seen.update(payload)
        return gen_response([{"text": "ok"}])

    chat_tools = [
        {
            "type": "function",
            "function": {
                "name": "play_card",
                "description": "play",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_index": {"type": "integer"},
                        "target_index": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
                    },
                    "required": ["card_index"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    make_provider(transport).complete([{"role": "user", "content": "x"}], tools=chat_tools)
    params = seen["tools"][0]["functionDeclarations"][0]["parameters"]
    # additionalProperties + default removed (Gemini 400s on them); anyOf kept
    assert "additionalProperties" not in params
    assert "default" not in params["properties"]["target_index"]
    assert params["properties"]["target_index"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert params["required"] == ["card_index"]


def test_endpoint_url_carries_model_and_method():
    provider = make_provider(lambda p: gen_response([{"text": "ok"}]))
    assert provider._endpoint_url() == "https://gen.test/v1beta/models/gemini-3.5-flash:generateContent"


def test_headers_use_goog_scheme():
    headers = make_provider(lambda p: None)._headers()
    assert headers["x-goog-api-key"] == "k"
    assert "Authorization" not in headers


def test_from_env_uses_google_key(monkeypatch):
    for var in ("STS_BENCH_BASE_URL", "STS_BENCH_MODEL", "STS_BENCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")
    provider = GeminiProvider.from_env(model="gemini-3.1-pro-preview")
    assert provider._api_key == "g-key"
    assert provider.base_url == GEMINI_BASE_URL
    assert provider.model == "gemini-3.1-pro-preview"
