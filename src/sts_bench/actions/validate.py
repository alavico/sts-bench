"""Legal-action validation: accept or reject a typed action against the state.

Rejection reasons are written for the model that proposed the action -- they
are the corrective feedback appended to the conversation on a retry, so they
say what was wrong *and* what would be legal instead.
"""

from __future__ import annotations

from dataclasses import dataclass

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

PROCEED_ALIASES = ("proceed", "confirm")
RETURN_ALIASES = ("return", "skip", "cancel", "leave")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None

    @staticmethod
    def accept() -> "Verdict":
        return Verdict(ok=True)

    @staticmethod
    def reject(reason: str) -> "Verdict":
        return Verdict(ok=False, reason=reason)


def validate(action: Action, message: StateMessage) -> Verdict:
    commands = set(message.available_commands)

    match action:
        case PlayCard():
            return _validate_play(action, message, commands)
        case EndTurn():
            if "end" not in commands:
                return Verdict.reject("cannot end turn now: not in a combat decision")
            return Verdict.accept()
        case Choose():
            return _validate_choose(action, message, commands)
        case UsePotion() | DiscardPotion():
            return _validate_potion(action, message, commands)
        case Proceed():
            if not commands.intersection(PROCEED_ALIASES):
                return Verdict.reject("cannot proceed now: no advance button on this screen")
            return Verdict.accept()
        case ReturnBack():
            if not commands.intersection(RETURN_ALIASES):
                return Verdict.reject("cannot go back now: no back/skip button on this screen")
            return Verdict.accept()
    return Verdict.reject(f"unknown action kind {action!r}")


def _validate_play(action: PlayCard, message: StateMessage, commands: set[str]) -> Verdict:
    if "play" not in commands:
        return Verdict.reject("cannot play cards now: not in combat or not your turn")
    combat = message.game_state.combat_state if message.game_state else None
    if combat is None:
        return Verdict.reject("cannot play cards now: no combat in progress")

    hand = combat.hand
    if not 0 <= action.card_index < len(hand):
        return Verdict.reject(f"card_index {action.card_index} is out of range: hand has {len(hand)} cards (0-{len(hand) - 1})")
    card = hand[action.card_index]
    if card.is_playable is False:
        return Verdict.reject(f"{card.name} is not playable right now (cost {card.cost}, you have {combat.player.energy} energy)")

    if card.has_target:
        if action.target_index is None:
            alive = [i for i, m in enumerate(combat.monsters) if _alive(m)]
            return Verdict.reject(f"{card.name} needs a target_index; valid targets: {alive}")
        return _validate_monster_target(action.target_index, message)
    if action.target_index is not None:
        return Verdict.reject(f"{card.name} does not take a target; omit target_index")
    return Verdict.accept()


def _validate_choose(action: Choose, message: StateMessage, commands: set[str]) -> Verdict:
    if "choose" not in commands:
        return Verdict.reject("cannot choose now: there is no choice list on this screen")
    choices = message.game_state.choice_list if message.game_state else []
    if not 0 <= action.choice_index < len(choices):
        listing = ", ".join(f"[{i}] {c}" for i, c in enumerate(choices))
        return Verdict.reject(f"choice_index {action.choice_index} is out of range; choices are: {listing}")
    return Verdict.accept()


def _validate_potion(action: UsePotion | DiscardPotion, message: StateMessage, commands: set[str]) -> Verdict:
    if "potion" not in commands:
        return Verdict.reject("cannot use or discard potions now")
    potions = message.game_state.potions if message.game_state else []
    if not 0 <= action.slot_index < len(potions):
        return Verdict.reject(f"slot_index {action.slot_index} is out of range: there are {len(potions)} potion slots")
    potion = potions[action.slot_index]

    if isinstance(action, DiscardPotion):
        if not potion.can_discard:
            return Verdict.reject(f"{potion.name} in slot {action.slot_index} cannot be discarded")
        return Verdict.accept()

    if not potion.can_use:
        return Verdict.reject(f"{potion.name} in slot {action.slot_index} cannot be used right now")
    if potion.requires_target:
        if action.target_index is None:
            return Verdict.reject(f"{potion.name} needs a target_index")
        return _validate_monster_target(action.target_index, message)
    if action.target_index is not None:
        return Verdict.reject(f"{potion.name} does not take a target; omit target_index")
    return Verdict.accept()


def _validate_monster_target(target_index: int, message: StateMessage) -> Verdict:
    combat = message.game_state.combat_state if message.game_state else None
    if combat is None:
        return Verdict.reject("cannot target a monster: no combat in progress")
    if not 0 <= target_index < len(combat.monsters):
        return Verdict.reject(f"target_index {target_index} is out of range: there are {len(combat.monsters)} monsters")
    monster = combat.monsters[target_index]
    if not _alive(monster):
        alive = [i for i, m in enumerate(combat.monsters) if _alive(m)]
        return Verdict.reject(f"{monster.name} (target_index {target_index}) is already gone; valid targets: {alive}")
    return Verdict.accept()


def _alive(monster) -> bool:
    return not monster.is_gone and not monster.half_dead and monster.current_hp > 0
