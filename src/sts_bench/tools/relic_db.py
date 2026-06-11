"""Static relic text: what a player reads when hovering the relic bar.

Same deal as the card DB: CommunicationMod sends relic identity (id, name,
counter) but not effect text. The pinned snapshot comes from the same
spire-archive parse (data/sts1/relics.json, trimmed to id/name/tier/text);
three counter-threshold relics (Happy Flower, Nunchaku, Sundial) lost their
numbers in the upstream parse and were corrected by hand at trim time,
values verified against the game.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from ..state.schema import Relic
from .card_db import _spell_energy, _squash

DATA_PATH = Path(__file__).parent / "data" / "sts1_relics.json"


@cache
def _index() -> tuple[dict[str, dict], dict[str, dict]]:
    entries = json.loads(DATA_PATH.read_text())
    for entry in entries:
        entry["text"] = _spell_energy(entry["text"])
    by_id: dict[str, dict] = {}
    for entry in entries:
        by_id.setdefault(entry["id"], entry)
        by_id.setdefault(_squash(entry["id"]), entry)
    by_name = {entry["name"].lower(): entry for entry in entries}
    return by_id, by_name


def relic_text(relic: Relic) -> str | None:
    """Effect text for a relic as the mod reports it; None when unknown."""
    by_id, by_name = _index()
    entry = (
        by_id.get(relic.id.upper().replace(" ", "_"))
        or by_id.get(_squash(relic.id))
        or by_name.get(relic.name.lower())
    )
    return entry["text"] if entry else None
