"""Floor rewards from boundary state deltas.

Reward lives in the environment layer: it is computed from what the game
reports at the floor's entry and exit, never from anything the agent said.
The inputs are the raw game_state dicts exactly as stored in the floor
record, so any trajectory ever logged can be re-scored under a newer spec --
which is why every Reward names the spec that produced it.

Spec v1 rewards terminal value only -- run won, floor progress, combat won,
HP (losing it is a step toward the loss condition) -- weighted to be readable
at a glance: clearing an ordinary combat at the cost of 20% max HP nets
roughly +2 combat -1 HP +1 floor. Instrumental resources (gold, relics, deck
quality) stay out: rewarding the gold *delta* would punish spending it,
pointing the gradient against shops. They remain metrics on the floor
scorecard instead. Components are emitted only when nonzero; the total is
their sum.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REWARD_SPEC_VERSION = "v1"


class Reward(BaseModel):
    """A floor's reward under one named spec.

    `components` keeps the per-signal breakdown so analyses can weigh signals
    differently without recomputing; `spec_version` says exactly which rules
    produced these numbers. A new spec re-derives both from the floor
    record's raw boundary states.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: str
    total: float
    components: dict[str, float] = Field(default_factory=dict)

COMBAT_ROOMS = {"MonsterRoom", "MonsterRoomElite", "MonsterRoomBoss"}

FLOOR_ADVANCED = 1.0
COMBAT_WON = 2.0
RUN_WON = 50.0
HP_WEIGHT = 5.0  # applied to the fraction of max HP gained/lost


def _victory(state: dict[str, Any]) -> bool:
    if state.get("screen_type") != "GAME_OVER":
        return False
    return bool((state.get("screen_state") or {}).get("victory"))


def floor_reward(entry_state: dict[str, Any], exit_state: dict[str, Any]) -> Reward:
    """Score one floor from its raw boundary game states.

    `entry_state` is the first state seen on the floor; `exit_state` is the
    state at which the floor ended -- the next floor's first state when the
    run moved on (its higher floor number is what marks the advance), or the
    run's final state (game over, disconnect) when it did not.
    """
    components: dict[str, float] = {}

    entry_floor = entry_state.get("floor") or 0
    exit_floor = exit_state.get("floor") or 0
    advanced = exit_floor > entry_floor
    won = _victory(exit_state)

    if advanced:
        components["floor_advanced"] = FLOOR_ADVANCED
    # A combat floor left alive is a combat won; the heart kill ends the run
    # on the same floor, so victory counts as leaving alive.
    if entry_state.get("room_type") in COMBAT_ROOMS and (advanced or won):
        components["combat_won"] = COMBAT_WON
    if won:
        components["run_won"] = RUN_WON

    entry_hp = entry_state.get("current_hp") or 0
    exit_hp = exit_state.get("current_hp") or 0
    max_hp = exit_state.get("max_hp") or entry_state.get("max_hp") or 0
    if exit_hp != entry_hp and max_hp:
        components["hp"] = round(HP_WEIGHT * (exit_hp - entry_hp) / max_hp, 4)

    return Reward(
        spec_version=REWARD_SPEC_VERSION,
        total=round(sum(components.values()), 4),
        components=components,
    )
