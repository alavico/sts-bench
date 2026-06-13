"""Floor agent conversation lifecycle: persist within a floor, reset across floors."""

import json
from pathlib import Path

from sts_bench.agents import FloorAgent
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


def test_conversation_persists_within_a_floor():
    provider = ScriptedProvider(
        [
            # card 3 in the fixture hand is Strike: targeted and playable
            tool_response(("play_card", {"card_index": 3, "target_index": 0})),
            tool_response(("end_turn", {})),
        ]
    )
    agent = FloorAgent(provider)
    combat = load("none-1.json")  # floor 1

    first = agent.decide(combat)
    assert first.action.kind == "play_card"
    agent.record("floor 1 COMBAT: play_card 3 (Strike) -> Jaw Worm [0]")
    agent.decide(combat)

    second = provider.requests[1]["messages"]
    # the whole first decision is still there: system, digest, the action call
    assert second[0]["role"] == "system"
    assert "<run>" in second[1]["content"]
    assert second[2]["tool_calls"][0]["function"]["name"] == "play_card"
    # the action call was closed with what the runner reported actually happened
    assert second[3] == {
        "role": "tool",
        "tool_call_id": "call_0",
        "content": "floor 1 COMBAT: play_card 3 (Strike) -> Jaw Worm [0]",
    }
    # then the fresh digest opens the next decision
    assert second[4]["role"] == "user" and "<run>" in second[4]["content"]


def test_floor_change_resets_to_system_plus_carried_summary():
    provider = ScriptedProvider(
        [
            tool_response(("end_turn", {})),
            tool_response(("return_back", {})),
        ]
    )
    agent = FloorAgent(provider)
    agent.decide(load("none-1.json"))  # floor 1
    agent.record("floor 1 COMBAT: end_turn")
    agent.decide(load("shop_screen-1.json"))  # floor 4

    fresh = provider.requests[1]["messages"]
    assert [m["role"] for m in fresh] == ["system", "user"]
    opening = fresh[1]["content"]
    assert "<previous_floor>1 decisions; last: floor 1 COMBAT: end_turn</previous_floor>" in opening
    # the old floor's combat digest did not survive the boundary
    assert "Jaw Worm" not in opening


def test_first_decision_has_no_carryover():
    provider = ScriptedProvider([tool_response(("end_turn", {}))])
    FloorAgent(provider).decide(load("none-1.json"))
    opening = provider.requests[0]["messages"][1]["content"]
    assert "<previous_floor>" not in opening and "<note>" not in opening


def test_forced_outcome_arrives_as_a_note_not_a_tool_result():
    provider = ScriptedProvider(
        [
            tool_response(("get_deck", {})),  # only observes: budget of 1 round runs out
            tool_response(("end_turn", {})),
        ]
    )
    agent = FloorAgent(provider, max_rounds=1)
    forced = agent.decide(load("none-1.json"))
    assert forced.action is None
    agent.record("floor 1 COMBAT: end_turn (forced)")
    agent.decide(load("none-1.json"))

    digest = provider.requests[1]["messages"][-1]
    assert digest["role"] == "user"
    assert digest["content"].startswith("<note>floor 1 COMBAT: end_turn (forced)</note>")


def test_unreported_outcome_is_closed_defensively():
    provider = ScriptedProvider(
        [tool_response(("end_turn", {})), tool_response(("end_turn", {}))]
    )
    agent = FloorAgent(provider)
    agent.decide(load("none-1.json"))
    agent.decide(load("none-1.json"))  # runner never called record()

    closing = provider.requests[1]["messages"][3]
    assert closing["role"] == "tool"
    assert closing["content"] == "(outcome not reported)"


def test_transcript_is_only_the_decisions_new_messages():
    provider = ScriptedProvider(
        [
            tool_response(("play_card", {"card_index": 3, "target_index": 0})),
            tool_response(("end_turn", {})),
        ]
    )
    agent = FloorAgent(provider)
    combat = load("none-1.json")

    first = agent.decide(combat)
    assert [m["role"] for m in first.transcript] == ["system", "user", "assistant"]
    agent.record("floor 1 COMBAT: play_card 3 (Strike) -> Jaw Worm [0]")

    second = agent.decide(combat)
    # closing tool result + fresh digest + the model's turn; nothing re-emitted
    assert [m["role"] for m in second.transcript] == ["tool", "user", "assistant"]


def test_sibling_calls_after_the_committed_action_are_closed():
    provider = ScriptedProvider(
        [tool_response(("end_turn", {}), ("get_deck", {}))]
    )
    decision = FloorAgent(provider).decide(load("none-1.json"))
    assert decision.action.kind == "end_turn"
    leftover = decision.transcript[-1]
    assert leftover["role"] == "tool" and leftover["tool_call_id"] == "call_1"
    assert "not executed" in leftover["content"]


def test_rejection_feedback_stays_in_the_floor_conversation():
    provider = ScriptedProvider(
        [
            tool_response(("play_card", {"card_index": 99})),
            tool_response(("end_turn", {})),
        ]
    )
    agent = FloorAgent(provider)
    decision = agent.decide(load("none-1.json"))
    assert decision.action.kind == "end_turn"
    assert decision.invalid_actions == 1
    feedback = provider.requests[1]["messages"][-1]
    assert feedback["role"] == "tool"
    assert "out of range" in feedback["content"]


def test_combat_is_briefed_once_with_the_deck_reference():
    provider = ScriptedProvider(
        [tool_response(("end_turn", {})), tool_response(("end_turn", {}))]
    )
    agent = FloorAgent(provider)
    combat = load("none-1.json")
    agent.decide(combat)
    agent.record("floor 1 COMBAT: end_turn")
    agent.decide(combat)

    first_digest = provider.requests[0]["messages"][1]["content"]
    assert "<relic_bar>" in first_digest
    assert "<deck_reference>" in first_digest
    assert "Deal 6 damage." in first_digest  # printed text, not just names
    # within the same combat the briefing is not repeated
    second_digest = provider.requests[1]["messages"][-1]["content"]
    assert "<deck_reference>" not in second_digest and "<relic_bar>" not in second_digest


def test_a_new_combat_on_the_same_floor_is_briefed_again():
    provider = ScriptedProvider(
        [
            tool_response(("end_turn", {})),
            tool_response(("proceed", {})),
            tool_response(("end_turn", {})),
        ]
    )
    agent = FloorAgent(provider)
    combat = load("none-1.json")
    reward = load("combat_reward-1.json")  # same floor 1, no combat_state

    agent.decide(combat)
    agent.record("floor 1 COMBAT: end_turn")
    agent.decide(reward)
    agent.record("floor 1 COMBAT_REWARD: proceed")
    agent.decide(combat)

    third_digest = provider.requests[2]["messages"][-1]["content"]
    assert "<deck_reference>" in third_digest


def test_usage_is_per_decision_not_cumulative():
    provider = ScriptedProvider(
        [tool_response(("end_turn", {})), tool_response(("end_turn", {}))]
    )
    agent = FloorAgent(provider)
    combat = load("none-1.json")
    first = agent.decide(combat)
    agent.record("floor 1 COMBAT: end_turn")
    second = agent.decide(combat)
    assert first.usage.prompt_tokens == 10
    assert second.usage.prompt_tokens == 10


def test_powers_define_themselves_once_on_first_appearance():
    raw = json.loads((FIXTURES / "none-1.json").read_text())
    raw["game_state"]["combat_state"]["monsters"][0]["powers"] = [
        {"id": "Split", "name": "Split", "amount": -1}
    ]
    hexed = json.loads(json.dumps(raw))
    hexed["game_state"]["combat_state"]["player"]["powers"] = [
        {"id": "Hex", "name": "Hex", "amount": 1}
    ]
    provider = ScriptedProvider([tool_response(("end_turn", {})) for _ in range(3)])
    agent = FloorAgent(provider)

    agent.decide(parse_message(raw))
    agent.record("floor 1 COMBAT: end_turn")
    agent.decide(parse_message(hexed))  # Hex lands mid-fight
    agent.record("floor 1 COMBAT: end_turn")
    agent.decide(parse_message(hexed))

    first = provider.requests[0]["messages"][1]["content"]
    assert "<power_definitions>" in first
    assert "splits into 2 smaller slimes" in first
    assert "Split -1" not in first  # the no-stacks sentinel never reaches the model

    second = provider.requests[1]["messages"][-1]["content"]
    assert "Dazed" in second  # the new arrival explains itself...
    assert "splits into" not in second  # ...but Split was defined a turn ago

    third = provider.requests[2]["messages"][-1]["content"]
    assert "<power_definitions>" not in third  # nothing new, nothing repeated
