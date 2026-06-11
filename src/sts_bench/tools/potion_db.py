"""Static potion text: what a player reads hovering the belt.

Same pinned spire-archive parse as cards and relics (data/sts1/potions.json,
trimmed to id/name/rarity/text). Upstream concatenates alternate printings
(Sacred Bark doubled text, achievement strings) behind digit separators; the
trim keeps the base printing only.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from ..state.schema import Potion
from .card_db import _squash

DATA_PATH = Path(__file__).parent / "data" / "sts1_potions.json"


@cache
def _index() -> tuple[dict[str, dict], dict[str, dict]]:
    entries = json.loads(DATA_PATH.read_text())
    by_id: dict[str, dict] = {}
    for entry in entries:
        by_id.setdefault(entry["id"], entry)
        by_id.setdefault(_squash(entry["id"]), entry)
    by_name = {entry["name"].lower(): entry for entry in entries}
    return by_id, by_name


def potion_text(potion: Potion) -> str | None:
    """Effect text for a potion as the mod reports it; None when unknown."""
    by_id, by_name = _index()
    entry = (
        by_id.get(potion.id.upper().replace(" ", "_"))
        or by_id.get(_squash(potion.id))
        or by_name.get(potion.name.lower())
    )
    return entry["text"] if entry else None
