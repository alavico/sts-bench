"""Provider-free baseline agents: the rulers LLM results are measured against.

A "floor reached" number means nothing alone. The random baseline pins the
zero point -- how far does pure chance get when every move it makes is legal?
The scripted baseline pins "competent reflexes, no strategy" -- it is the same
policy the harness plays when an LLM decision is forced, promoted to a full
agent. Both speak the agent protocol (decide/record), so the runner drives
them exactly like an LLM scaffold, and both cost zero tokens.
"""

from __future__ import annotations

import random

from ..actions.schema import (
    Action,
    Choose,
    DiscardPotion,
    EndTurn,
    PlayCard,
    Proceed,
    ReturnBack,
    UsePotion,
)
from ..actions.validate import validate
from ..state.schema import StateMessage
from .base import Decision


def legal_actions(message: StateMessage) -> list[Action]:
    """Every concrete action the validator accepts on this state.

    Candidates are generated exhaustively from the state's own index spaces
    (hand x monsters, choices, potion slots) and filtered through the one
    legality oracle the LLM agents face, so "random-legal" means uniform over
    exactly what a model would have been allowed to do.
    """
    state = message.game_state
    combat = state.combat_state if state else None
    targets = range(len(combat.monsters)) if combat else range(0)

    candidates: list[Action] = [EndTurn(), Proceed(), ReturnBack()]
    if combat is not None:
        for i, card in enumerate(combat.hand):
            if card.has_target:
                candidates.extend(PlayCard(card_index=i, target_index=t) for t in targets)
            else:
                candidates.append(PlayCard(card_index=i))
    if state is not None:
        candidates.extend(Choose(choice_index=i) for i in range(len(state.choice_list)))
        for slot, potion in enumerate(state.potions):
            if potion.requires_target:
                candidates.extend(UsePotion(slot_index=slot, target_index=t) for t in targets)
            else:
                candidates.append(UsePotion(slot_index=slot))
            candidates.append(DiscardPotion(slot_index=slot))
    return [action for action in candidates if validate(action, message).ok]


def scripted_action(message: StateMessage) -> Action | None:
    """The smoke policy: play the first playable card, otherwise advance.

    Deliberately trivial -- attack whatever is alive, take choice 0, move on.
    None when nothing in its repertoire applies (e.g. the start screen).
    """
    commands = set(message.available_commands)
    state = message.game_state
    if "play" in commands and state is not None and state.combat_state is not None:
        combat = state.combat_state
        target = next(
            (i for i, m in enumerate(combat.monsters) if not m.is_gone and not m.half_dead and m.current_hp > 0),
            None,
        )
        for i, card in enumerate(combat.hand):
            if not card.is_playable:
                continue
            if card.has_target:
                if target is None:
                    continue
                return PlayCard(card_index=i, target_index=target)
            return PlayCard(card_index=i)
    if "end" in commands:
        return EndTurn()
    if "choose" in commands:
        return Choose(choice_index=0)
    if commands.intersection(("proceed", "confirm")):
        return Proceed()
    return None


class HeuristicAgent:
    """Shared shape: a decision that costs nothing and carries no conversation."""

    def record(self, line: str) -> None:
        """Agent protocol; a heuristic has no memory to feed."""

    def decide(self, message: StateMessage) -> Decision:
        action = self._choose(message)
        if action is None:
            return Decision(action=None, forced_reason="no legal action in repertoire")
        return Decision(action=action)

    def _choose(self, message: StateMessage) -> Action | None:
        raise NotImplementedError


class RandomAgent(HeuristicAgent):
    """Uniform over every validator-accepted action: the zero point of the scale.

    Seed the RNG to make the pick sequence reproducible -- with a fixed game
    seed the whole run replays identically.
    """

    def __init__(self, rng_seed: int | str | None = None):
        self._rng = random.Random(rng_seed)

    def _choose(self, message: StateMessage) -> Action | None:
        actions = legal_actions(message)
        return self._rng.choice(actions) if actions else None


class ScriptedAgent(HeuristicAgent):
    """The scripted policy as an agent: reflexes a model must beat to claim strategy.

    When the preferred pick is rejected (e.g. choice 0 is a potion onto a full
    belt), it falls through the same advance order the raw fallback uses.
    """

    def _choose(self, message: StateMessage) -> Action | None:
        action = scripted_action(message)
        if action is not None and validate(action, message).ok:
            return action
        for fallback in (EndTurn(), Proceed(), ReturnBack(), Choose(choice_index=0)):
            if validate(fallback, message).ok:
                return fallback
        return None
