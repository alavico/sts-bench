"""Zero-shot agent loop against a scripted provider: observe, act, recover, give up."""

import json
from pathlib import Path

import pytest

from sts_bench.agents import ZeroShotAgent
from sts_bench.providers.base import ModelResponse, ToolCall, Usage
from sts_bench.state import parse_message

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def load(name):
    return parse_message(json.loads((FIXTURES / name).read_text()))


class ScriptedProvider:
    """Returns canned responses in order; records every request it sees."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, messages, tools=None):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.responses.pop(0)


def tool_response(*calls, usage=Usage(prompt_tokens=10, completion_tokens=5)):
    tool_calls = tuple(
        ToolCall(id=f"call_{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls)
    )
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in tool_calls
        ],
    }
    return ModelResponse(message=message, text=None, tool_calls=tool_calls, usage=usage)


def text_response(text):
    return ModelResponse(message={"role": "assistant", "content": text}, text=text)


def test_observation_then_action():
    provider = ScriptedProvider(
        [
            tool_response(("get_deck", {})),
            # card 3 in the fixture hand is Strike: targeted and playable
            tool_response(("play_card", {"card_index": 3, "target_index": 0})),
        ]
    )
    decision = ZeroShotAgent(provider).decide(load("none-1.json"))

    assert decision.action is not None and decision.action.kind == "play_card"
    assert decision.observation_calls == 1
    assert decision.invalid_actions == 0
    assert decision.usage.prompt_tokens == 20
    # the observation result was fed back as a tool message before the second call
    second = provider.requests[1]["messages"]
    assert second[-1]["role"] == "tool"
    assert "deck" in second[-1]["content"]


def test_recorded_history_shows_up_in_the_next_prompt():
    provider = ScriptedProvider([tool_response(("end_turn", {}))])
    agent = ZeroShotAgent(provider)
    agent.record("floor 6 SHOP_ROOM: choose 0 (shop)")
    agent.record("floor 6 SHOP_SCREEN: return_back")
    agent.decide(load("none-1.json"))

    view = provider.requests[0]["messages"][1]["content"]
    assert "<recent_decisions>" in view
    # oldest first, so the model reads the trail in play order
    assert view.index("choose 0 (shop)") < view.index("return_back")


def test_no_history_means_no_section():
    provider = ScriptedProvider([tool_response(("end_turn", {}))])
    ZeroShotAgent(provider).decide(load("none-1.json"))
    assert "<recent_decisions>" not in provider.requests[0]["messages"][1]["content"]


def test_history_keeps_only_the_most_recent_entries():
    provider = ScriptedProvider([tool_response(("end_turn", {}))])
    agent = ZeroShotAgent(provider, history_size=2)
    for i in range(4):
        agent.record(f"entry-{i}")
    agent.decide(load("none-1.json"))

    view = provider.requests[0]["messages"][1]["content"]
    trail = view.split("<recent_decisions>")[1]
    assert "entry-0" not in trail and "entry-1" not in trail
    assert "entry-2" in trail and "entry-3" in trail


def test_first_message_is_cursory_view_and_tools_are_offered():
    provider = ScriptedProvider([tool_response(("end_turn", {}))])
    ZeroShotAgent(provider).decide(load("none-1.json"))
    request = provider.requests[0]
    assert request["messages"][0]["role"] == "system"
    assert "<run>" in request["messages"][1]["content"]
    names = {t["function"]["name"] for t in request["tools"]}
    assert "play_card" in names and "get_map" in names


def test_invalid_action_gets_rejection_feedback_then_retries():
    provider = ScriptedProvider(
        [
            tool_response(("play_card", {"card_index": 99})),
            tool_response(("end_turn", {})),
        ]
    )
    decision = ZeroShotAgent(provider).decide(load("none-1.json"))

    assert decision.action.kind == "end_turn"
    assert decision.invalid_actions == 1
    feedback = provider.requests[1]["messages"][-1]
    assert feedback["role"] == "tool"
    assert "out of range" in feedback["content"]


def test_plain_text_gets_nudged():
    provider = ScriptedProvider([text_response("I think I should attack."), tool_response(("end_turn", {}))])
    decision = ZeroShotAgent(provider).decide(load("none-1.json"))
    assert decision.action.kind == "end_turn"
    nudge = provider.requests[1]["messages"][-1]
    assert nudge["role"] == "user"


def test_round_budget_exhausted_forces_fallback():
    provider = ScriptedProvider([tool_response(("get_deck", {}))] * 3)
    decision = ZeroShotAgent(provider, max_rounds=3).decide(load("none-1.json"))
    assert decision.action is None
    assert "3 rounds" in decision.forced_reason
    assert decision.observation_calls == 3


def test_invalid_budget_exhausted_forces_fallback():
    bad = tool_response(("play_card", {"card_index": 99}))
    provider = ScriptedProvider([bad] * 10)
    decision = ZeroShotAgent(provider, max_invalid=2).decide(load("none-1.json"))
    assert decision.action is None
    assert "invalid actions" in decision.forced_reason


def test_malformed_arguments_fed_back_as_error():
    broken = ToolCall(id="c1", name="choose", arguments=None, arguments_error="arguments were not valid JSON")
    response = ModelResponse(
        message={"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "choose", "arguments": "{oops"}}]},
        text=None,
        tool_calls=(broken,),
    )
    provider = ScriptedProvider([response, tool_response(("choose", {"choice_index": 0}))])
    decision = ZeroShotAgent(provider).decide(load("event-1.json"))
    assert decision.action.kind == "choose"
    assert decision.invalid_actions == 1
