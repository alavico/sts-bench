"""The tool surface an agent gets: action tools + observation tools.

Action tools are generated from the typed action models (single source of
truth: the same Pydantic classes that the validator and translator consume),
so a parsed tool call lands directly in an `Action`. Observation tools answer
from the already-held state -- calling one never advances the game.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import ValidationError

from ..actions.schema import (
    ActionBase,
    Action,
    Choose,
    DiscardPotion,
    EndTurn,
    PlayCard,
    Proceed,
    ReturnBack,
    UsePotion,
)
from ..state.schema import StateMessage
from . import observations


class ToolError(Exception):
    """A tool call the registry cannot honor; the message is model-facing."""


ACTION_DESCRIPTIONS: dict[type[ActionBase], str] = {
    PlayCard: (
        "Play a card from your hand. card_index is the 0-based position shown in the "
        "hand listing; cards marked [needs target] also require target_index, the "
        "0-based enemy position."
    ),
    EndTurn: "End your combat turn.",
    Choose: "Pick an option from the current choice list by its 0-based choice_index.",
    UsePotion: (
        "Drink the potion in the given 0-based slot. Potions that need a target also "
        "require target_index, the 0-based enemy position."
    ),
    DiscardPotion: "Throw away the potion in the given 0-based slot without using it.",
    Proceed: "Advance past the current screen (confirm / continue / leave).",
    ReturnBack: "Back out of or skip the current screen.",
}

OBSERVATIONS: dict[str, tuple[str, Callable[[StateMessage], str]]] = {
    "get_deck": ("List every card in your deck.", observations.get_deck),
    "get_draw_pile": (
        "List the cards remaining in your draw pile (contents only; draw order is hidden, as in the real game).",
        observations.get_draw_pile,
    ),
    "get_discard_pile": ("List the cards in your discard pile.", observations.get_discard_pile),
    "get_exhaust_pile": ("List the cards in your exhaust pile.", observations.get_exhaust_pile),
    "get_relics": ("List your relics.", observations.get_relics),
    "get_potions": ("List your potion slots and whether each potion is usable.", observations.get_potions),
    "get_map": (
        "Show the full act map as floor-by-floor adjacency: which rooms exist and which "
        "rooms each connects to above it. Use this to plan your route.",
        observations.get_map,
    ),
    "get_legal_actions": (
        "List which action tools are legal right now.",
        observations.get_legal_actions,
    ),
}


@dataclass(frozen=True)
class Tool:
    name: str
    kind: Literal["action", "observation"]
    description: str
    parameters: dict[str, Any]
    action_model: type[ActionBase] | None = None


class ToolRegistry:
    def __init__(self, include_observations: bool = True):
        self._tools: dict[str, Tool] = {}
        for model, description in ACTION_DESCRIPTIONS.items():
            name = model.model_fields["kind"].default
            self._tools[name] = Tool(
                name=name,
                kind="action",
                description=description,
                parameters=_action_parameters(model),
                action_model=model,
            )
        if include_observations:
            for name, (description, _handler) in OBSERVATIONS.items():
                self._tools[name] = Tool(
                    name=name,
                    kind="observation",
                    description=description,
                    parameters={"type": "object", "properties": {}, "additionalProperties": False},
                )

    def openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def kind_of(self, name: str) -> Literal["action", "observation"] | None:
        tool = self._tools.get(name)
        return tool.kind if tool else None

    def observe(self, name: str, message: StateMessage) -> str:
        if name not in OBSERVATIONS or name not in self._tools:
            raise ToolError(f"unknown observation tool {name!r}")
        return OBSERVATIONS[name][1](message)

    def parse_action(self, name: str, arguments: dict[str, Any]) -> Action:
        tool = self._tools.get(name)
        if tool is None or tool.action_model is None:
            known = ", ".join(t.name for t in self._tools.values() if t.kind == "action")
            raise ToolError(f"{name!r} is not an action tool; action tools are: {known}")
        try:
            return tool.action_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ToolError(f"invalid arguments for {name}: {problems}") from exc


def _action_parameters(model: type[ActionBase]) -> dict[str, Any]:
    """JSON-schema parameters for an action model, minus the `kind` discriminator.

    `kind` is the tool *name* on the wire, not an argument the model fills in.
    """
    schema = model.model_json_schema()
    properties = {
        name: _strip_titles(prop)
        for name, prop in schema.get("properties", {}).items()
        if name != "kind"
    }
    required = [name for name in schema.get("required", []) if name != "kind"]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _strip_titles(prop: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: v for k, v in prop.items() if k != "title"}
    if "anyOf" in cleaned:
        cleaned["anyOf"] = [_strip_titles(p) for p in cleaned["anyOf"]]
    return cleaned
