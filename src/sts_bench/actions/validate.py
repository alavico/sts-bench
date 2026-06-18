"""Legal-action validation: accept or reject a typed action against the state.

Rejection reasons are written for the model that proposed the action -- they
are the corrective feedback appended to the conversation on a retry, so they
say what was wrong *and* what would be legal instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..state.schema import CardRewardScreen, CombatRewardScreen, ShopScreen, StateMessage
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


def card_reward_skip_index(message: StateMessage) -> int | None:
    """The `choose` index that means "skip this card reward".

    Skip lives in the choose namespace as the slot just past the last card, so
    the model declines a card the same way it takes one -- one verb, one index --
    instead of switching to return_back, which buries skip outside the numbered
    comparison. The reward size varies (Question Card adds a choice, Busted
    Crown removes two, Singing Bowl adds a +2 Max HP option), so the index is
    derived from the live choice list, never hardcoded. None when this screen
    has no skippable card reward.
    """
    state = message.game_state
    if state is None or not isinstance(state.screen, CardRewardScreen):
        return None
    if not state.screen.skip_available:
        return None
    if not set(message.available_commands).intersection(RETURN_ALIASES):
        return None
    return len(state.choice_list)


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
    # The skip pseudo-index declines the reward -- no card, so no potion check.
    if action.choice_index == card_reward_skip_index(message):
        return Verdict.accept()
    state = message.game_state
    choices = state.choice_list if state else []
    if not 0 <= action.choice_index < len(choices):
        listing = ", ".join(f"[{i}] {c}" for i, c in enumerate(choices))
        return Verdict.reject(f"choice_index {action.choice_index} is out of range; choices are: {listing}")
    return _validate_potion_capacity(action, state, choices)


def _validate_potion_capacity(action: Choose, state, choices: list[str]) -> Verdict:
    """A potion cannot be taken or bought onto a full belt. The game greys the
    click out, and CommunicationMod hangs on it (observed live: the run died
    on a 60s step timeout), so this must never reach the wire."""
    if state is None or not state.potions_full:
        return Verdict.accept()
    slots = len(state.potions)
    full = f"potion belt is full ({slots}/{slots})"

    screen = state.screen
    if isinstance(screen, CombatRewardScreen):
        rewards = screen.rewards
        if action.choice_index < len(rewards) and rewards[action.choice_index].reward_type == "POTION":
            return Verdict.reject(
                f"cannot take the potion: {full}; discard_potion to make room, "
                "take another reward, or proceed"
            )
    if isinstance(screen, ShopScreen):
        chosen = choices[action.choice_index]
        if any(p.name.lower() == chosen for p in screen.potions):
            return Verdict.reject(
                f"cannot buy {chosen}: {full}; discard_potion to make room first"
            )
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
