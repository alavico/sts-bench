"""Typed models for CommunicationMod state messages.

Written against what the mod's serializer can send (CommunicationMod's
GameStateConverter is the ground truth; spirecomm's models are the readable
reference), validated against real captured payloads in tests/fixtures/states/.

The schema is deliberately strict: `extra="forbid"` everywhere and enums for
closed sets, so any message shape we have not modeled fails *loudly* instead
of being silently coerced. The recovery path is the point -- a StateParseError
carries the raw payload, the offending session gets harvested into a fixture,
and the schema grows exactly where coverage was thin. Screens we have never
captured (shop, rest, chest, ...) are unmodeled on purpose; see SCREEN_MODELS.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class StateParseError(Exception):
    """A message didn't fit the schema. `raw` holds the full payload for harvesting."""

    def __init__(self, message: str, raw: dict[str, Any]):
        super().__init__(message)
        self.raw = raw


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# -- enums (closed sets we are confident about; wrong on purpose = loud) ------


class ScreenType(str, enum.Enum):
    NONE = "NONE"
    EVENT = "EVENT"
    CHEST = "CHEST"
    SHOP_ROOM = "SHOP_ROOM"
    REST = "REST"
    CARD_REWARD = "CARD_REWARD"
    COMBAT_REWARD = "COMBAT_REWARD"
    MAP = "MAP"
    BOSS_REWARD = "BOSS_REWARD"
    SHOP_SCREEN = "SHOP_SCREEN"
    GRID = "GRID"
    HAND_SELECT = "HAND_SELECT"
    GAME_OVER = "GAME_OVER"
    COMPLETE = "COMPLETE"
    MAIN_MENU = "MAIN_MENU"


class CardType(str, enum.Enum):
    ATTACK = "ATTACK"
    SKILL = "SKILL"
    POWER = "POWER"
    STATUS = "STATUS"
    CURSE = "CURSE"


class CardRarity(str, enum.Enum):
    BASIC = "BASIC"
    COMMON = "COMMON"
    UNCOMMON = "UNCOMMON"
    RARE = "RARE"
    SPECIAL = "SPECIAL"
    CURSE = "CURSE"


class Intent(str, enum.Enum):
    ATTACK = "ATTACK"
    ATTACK_BUFF = "ATTACK_BUFF"
    ATTACK_DEBUFF = "ATTACK_DEBUFF"
    ATTACK_DEFEND = "ATTACK_DEFEND"
    BUFF = "BUFF"
    DEBUFF = "DEBUFF"
    STRONG_DEBUFF = "STRONG_DEBUFF"
    DEBUG = "DEBUG"
    DEFEND = "DEFEND"
    DEFEND_BUFF = "DEFEND_BUFF"
    DEFEND_DEBUFF = "DEFEND_DEBUFF"
    ESCAPE = "ESCAPE"
    MAGIC = "MAGIC"
    NONE = "NONE"
    SLEEP = "SLEEP"
    STUN = "STUN"
    UNKNOWN = "UNKNOWN"


# -- shared pieces -------------------------------------------------------------


class Card(Base):
    id: str
    uuid: str
    name: str
    type: CardType
    rarity: CardRarity
    cost: int
    upgrades: int
    has_target: bool
    is_playable: bool | None = None  # only sent for cards in hand during combat
    exhausts: bool
    ethereal: bool


class Power(Base):
    # No fixture coverage yet (every captured powers[] was empty); shape taken
    # from spirecomm. First real power in a harvest will confirm or correct it.
    id: str
    name: str
    amount: int


class Orb(Base):
    id: str | None = None  # empty orb slots ("Empty Slot") carry no id
    name: str
    evoke_amount: int
    passive_amount: int


class Potion(Base):
    id: str
    name: str
    can_use: bool
    can_discard: bool
    requires_target: bool


class Relic(Base):
    id: str
    name: str
    counter: int


class Monster(Base):
    id: str
    name: str
    current_hp: int
    max_hp: int
    block: int
    intent: Intent
    half_dead: bool
    is_gone: bool
    move_id: int
    last_move_id: int | None = None
    second_last_move_id: int | None = None
    move_base_damage: int
    move_adjusted_damage: int  # -1 when the game won't reveal the number
    move_hits: int
    powers: list[Power] = Field(default_factory=list)


class PlayerCombat(Base):
    current_hp: int
    max_hp: int
    block: int
    energy: int
    orbs: list[Orb] = Field(default_factory=list)
    powers: list[Power] = Field(default_factory=list)


class CombatState(Base):
    turn: int
    player: PlayerCombat
    monsters: list[Monster]
    hand: list[Card]
    draw_pile: list[Card]
    discard_pile: list[Card]
    exhaust_pile: list[Card]
    limbo: list[Card] = Field(default_factory=list)
    cards_discarded_this_turn: int
    times_damaged: int


class NodeRef(Base):
    x: int
    y: int


class MapNode(Base):
    x: int
    y: int
    symbol: str  # M monster, E elite, $ shop, R rest, T chest, ? event, B boss
    children: list[NodeRef] = Field(default_factory=list)
    parents: list[NodeRef] = Field(default_factory=list)


class MapNodeWithSymbol(Base):
    x: int
    y: int
    symbol: str | None = None  # the pre-act virtual node {x: 0, y: -1} has none


# -- per-screen payloads -------------------------------------------------------


class NoneScreen(Base):
    pass


class EventOption(Base):
    choice_index: int | None = None  # absent on disabled/locked options
    text: str
    label: str
    disabled: bool


class EventScreen(Base):
    event_id: str
    event_name: str
    body_text: str
    options: list[EventOption]


class MapScreen(Base):
    current_node: MapNodeWithSymbol | None = None
    next_nodes: list[MapNodeWithSymbol] = Field(default_factory=list)
    first_node_chosen: bool
    boss_available: bool


class CardRewardScreen(Base):
    cards: list[Card]
    bowl_available: bool
    skip_available: bool


class RewardItem(Base):
    reward_type: str  # GOLD, POTION, RELIC, CARD, STOLEN_GOLD, EMERALD_KEY, ...
    gold: int | None = None
    potion: Potion | None = None
    relic: Relic | None = None
    link: Relic | None = None


class CombatRewardScreen(Base):
    rewards: list[RewardItem]


class GridScreen(Base):
    cards: list[Card]
    selected_cards: list[Card] = Field(default_factory=list)
    num_cards: int
    any_number: bool
    confirm_up: bool
    for_purge: bool
    for_transform: bool
    for_upgrade: bool


# Screens we have modeled. A screen type missing here parses to StateParseError
# on first contact -- harvest the session, model the screen, add it.
SCREEN_MODELS: dict[ScreenType, type[Base]] = {
    ScreenType.NONE: NoneScreen,
    ScreenType.EVENT: EventScreen,
    ScreenType.MAP: MapScreen,
    ScreenType.CARD_REWARD: CardRewardScreen,
    ScreenType.COMBAT_REWARD: CombatRewardScreen,
    ScreenType.GRID: GridScreen,
}

Screen = NoneScreen | EventScreen | MapScreen | CardRewardScreen | CombatRewardScreen | GridScreen


# -- top level -----------------------------------------------------------------


class GameState(Base):
    character: str = Field(alias="class")
    ascension_level: int
    seed: int
    act: int
    act_boss: str
    floor: int
    current_hp: int
    max_hp: int
    gold: int
    deck: list[Card]
    relics: list[Relic]
    potions: list[Potion]
    map: list[MapNode]
    combat_state: CombatState | None = None
    choice_list: list[str] = Field(default_factory=list)
    room_type: str
    room_phase: str
    action_phase: str
    is_screen_up: bool
    screen_type: ScreenType
    screen_name: str
    screen_state: dict[str, Any] = Field(default_factory=dict)
    # Populated from screen_state by the validator below; not part of the wire format.
    screen: Screen | None = None

    @model_validator(mode="after")
    def _parse_screen(self) -> "GameState":
        model = SCREEN_MODELS.get(self.screen_type)
        if model is None:
            raise ValueError(
                f"screen_type {self.screen_type.value} has no model yet -- "
                f"harvest this session into a fixture and extend SCREEN_MODELS"
            )
        self.screen = model.model_validate(self.screen_state)
        return self


class StateMessage(Base):
    available_commands: list[str]
    ready_for_command: bool
    in_game: bool
    game_state: GameState | None = None


def parse_message(raw: dict[str, Any]) -> StateMessage:
    """Parse one inbound message; failures carry the raw payload for harvesting."""
    try:
        return StateMessage.model_validate(raw)
    except (ValidationError, ValueError) as exc:
        raise StateParseError(f"state message does not fit schema: {exc}", raw=raw) from exc
