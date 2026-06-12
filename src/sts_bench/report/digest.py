"""Read facts back out of the digest text the model saw.

The structured trajectory records carry the floor economics; the course of a
fight and the route across the map live only in the stored conversations --
in the cursory-view digests, our own serializer's output. These parsers mine
them with that grammar in mind, and tolerantly: a line that does not match is
skipped, so a serializer tweak degrades the report instead of breaking it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_RUN_LINE = re.compile(r"act (\d+) floor (\d+) \| HP (\d+)/(\d+) \| gold (\d+)")
_TURN = re.compile(r"^turn (\d+) \| energy (\d+) \| block (\d+)$", re.MULTILINE)
_ENEMY = re.compile(
    r"^enemy\[(\d+)\] (.+?) (\d+)/(\d+)(?: block (\d+))? \| intent (.+)$",
    re.MULTILINE,
)
_AT_NODE = re.compile(r"^at node \S+ \(x=(\d+)\)$", re.MULTILINE)
_CHOICE_X = re.compile(r"^\[(\d+)\] x=(\d+)$", re.MULTILINE)


@dataclass
class EnemySnapshot:
    name: str
    hp: int
    max_hp: int
    block: int
    intent: str


@dataclass
class CombatSnapshot:
    """What one digest's combat section said, plus the HP off its run line."""

    turn: int
    energy: int
    block: int
    hp: int | None = None
    max_hp: int | None = None
    enemies: list[EnemySnapshot] = field(default_factory=list)


def combat_snapshot(text: str) -> CombatSnapshot | None:
    """The combat state one digest showed; None when the text has no fight."""
    start = text.find("<combat>")
    if start < 0:
        return None
    end = text.find("</combat>", start)
    section = text[start : end if end >= 0 else len(text)]
    turn = _TURN.search(section)
    if turn is None:
        return None
    snapshot = CombatSnapshot(turn=int(turn[1]), energy=int(turn[2]), block=int(turn[3]))
    run = _RUN_LINE.search(text)
    if run is not None:
        snapshot.hp, snapshot.max_hp = int(run[3]), int(run[4])
    for match in _ENEMY.finditer(section):
        snapshot.enemies.append(
            EnemySnapshot(
                name=match[2],
                hp=int(match[3]),
                max_hp=int(match[4]),
                block=int(match[5] or 0),
                intent=match[6],
            )
        )
    return snapshot


@dataclass
class MapChoice:
    """A MAP digest read back: where we stood and which x each choice led to.

    The boss row renders as a bare `[0] boss` choice, so `options` is empty
    there and `boss` is the whole story.
    """

    at_x: int | None
    boss: bool
    options: dict[int, int]  # choice index -> node x


def map_choice(text: str) -> MapChoice | None:
    """The route information one MAP digest carried; None off the map screen."""
    if '<screen type="MAP">' not in text:
        return None
    at = _AT_NODE.search(text)
    options = {int(m[1]): int(m[2]) for m in _CHOICE_X.finditer(text)}
    return MapChoice(
        at_x=int(at[1]) if at else None,
        boss="boss fight available" in text,
        options=options,
    )
