"""Accepted action -> CommunicationMod command string.

Owns the wire quirks so nothing else has to know them: PLAY hand indices are
1-based (everything else 0-based), potions use the `potion use|discard` form,
and proceed/return map to whichever alias the current screen offers.
Call validate() first; translation assumes the action is legal.
"""

from __future__ import annotations

from ..state.schema import StateMessage
from .schema import (
    Action,
    Choose,
    DiscardPotion,
    EndTurn,
    PlayCard,
    Proceed,
    ReturnBack,
    UsePotion,
)
from .validate import PROCEED_ALIASES, RETURN_ALIASES


def translate(action: Action, message: StateMessage) -> str:
    commands = set(message.available_commands)
    match action:
        case PlayCard():
            cmd = f"play {action.card_index + 1}"
            return cmd if action.target_index is None else f"{cmd} {action.target_index}"
        case EndTurn():
            return "end"
        case Choose():
            return f"choose {action.choice_index}"
        case UsePotion():
            cmd = f"potion use {action.slot_index}"
            return cmd if action.target_index is None else f"{cmd} {action.target_index}"
        case DiscardPotion():
            return f"potion discard {action.slot_index}"
        case Proceed():
            return _first_available(PROCEED_ALIASES, commands)
        case ReturnBack():
            return _first_available(RETURN_ALIASES, commands)
    raise ValueError(f"untranslatable action: {action!r}")


def _first_available(aliases: tuple[str, ...], commands: set[str]) -> str:
    for alias in aliases:
        if alias in commands:
            return alias
    raise ValueError(f"none of {aliases} available; validate() should have rejected this")
