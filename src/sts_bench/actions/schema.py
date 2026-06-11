"""Typed actions: the only way an agent affects the game.

Each model here doubles as an LLM tool definition: the tool name is `kind`,
the arguments are the fields, and parsed tool calls land back in these types.
All indices are 0-based as the agent sees them (hand position, monster
position, choice list, potion slot) -- the translator owns the quirks of
CommunicationMod's wire format (e.g. PLAY is 1-based).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlayCard(ActionBase):
    kind: Literal["play_card"] = "play_card"
    card_index: int  # position in hand
    target_index: int | None = None  # position in the monsters list


class EndTurn(ActionBase):
    kind: Literal["end_turn"] = "end_turn"


class Choose(ActionBase):
    kind: Literal["choose"] = "choose"
    choice_index: int  # position in the current choice list


class UsePotion(ActionBase):
    kind: Literal["use_potion"] = "use_potion"
    slot_index: int
    target_index: int | None = None


class DiscardPotion(ActionBase):
    kind: Literal["discard_potion"] = "discard_potion"
    slot_index: int


class Proceed(ActionBase):
    kind: Literal["proceed"] = "proceed"


class ReturnBack(ActionBase):
    kind: Literal["return_back"] = "return_back"


Action = Annotated[
    Union[PlayCard, EndTurn, Choose, UsePotion, DiscardPotion, Proceed, ReturnBack],
    Field(discriminator="kind"),
]
