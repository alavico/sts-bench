"""Static card text: what a player reads on the card face.

CommunicationMod sends card identity (id, name, cost, flags) but not rules
text -- the numbers live in game code, so without this the model plays from
memory of the game rather than from what is on screen. This module carries a
pinned snapshot of every card's printed text (base and upgraded), parsed from
the game files by the spire-archive project.

Source: https://github.com/nkhoit/spire-archive, data/sts1/cards.json
(trimmed to id/name/color/type/text). Re-pin by re-running the trim against a
newer snapshot if the game version moves. Combat-generated cards (Shiv,
Miracle, Beta, ...) are absent upstream; they live in the hand-curated
sts1_cards_supplement.json, verified against the community wiki.

Text is the card's *base* printing: buff-adjusted numbers (the game's green
text) are not computed here -- the combat digest carries the buffs themselves.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

from ..state.schema import Card

DATA_DIR = Path(__file__).parent / "data"
DATA_PATHS = (DATA_DIR / "sts1_cards.json", DATA_DIR / "sts1_cards_supplement.json")

# In-game text shows energy as colored orb glyphs; the datasets keep them as
# [R]/[G]/[B] markers (cards) or [Energy] (relics). A player reads orbs as
# energy, so spell it out.
_GLYPH = r"\[(?:[A-Z]|Energy)\]"
_ENERGY_RUN = re.compile(rf"{_GLYPH}(?: {_GLYPH})*")


def _spell_energy(text: str | None) -> str | None:
    if not text:
        return text

    def sub(match: re.Match) -> str:
        count = match.group(0).count("[")
        before = text[: match.start()].rstrip().lower()
        after = text[match.end() :].lstrip().lower()
        # "gain X [G]", "1 less [R]", "[B] equal to its cost": the glyph is a
        # unit, not an amount
        if before.endswith((" x", "x+1", "less", "additional")) or after.startswith("equal to"):
            return "Energy"
        return f"{count} Energy"

    return _ENERGY_RUN.sub(sub, text)


@cache
def _index() -> tuple[dict[str, dict], dict[str, dict]]:
    entries = [e for path in DATA_PATHS for e in json.loads(path.read_text())]
    for entry in entries:
        entry["text"] = _spell_energy(entry["text"])
        entry["upgraded_text"] = _spell_energy(entry["upgraded_text"])
    by_id: dict[str, dict] = {}
    for entry in entries:
        # Index ids both as-is and separator-free: the wire mixes styles
        # ("Strike_R", "Searing Blow", "ThroughViolence") and the dataset
        # uses enum style ("THROUGH_VIOLENCE").
        by_id.setdefault(entry["id"], entry)
        by_id.setdefault(_squash(entry["id"]), entry)
    # Display names collide across characters (every class has a Strike) but
    # collisions share their text, so a flat name index is safe.
    by_name = {entry["name"].lower(): entry for entry in entries}
    return by_id, by_name


def _squash(card_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", card_id.upper())


def card_text(card: Card) -> str | None:
    """Printed rules text for a card as the mod reports it; None when unknown.

    Upgraded cards (the mod marks them in both `name` and `upgrades`) get the
    upgraded printing.
    """
    by_id, by_name = _index()
    entry = (
        by_id.get(card.id.upper().replace(" ", "_"))
        or by_id.get(_squash(card.id))
        or by_name.get(card.name.rstrip("+0123456789").strip().lower())
    )
    if entry is None:
        return None
    upgraded = card.upgrades > 0 or card.name.endswith("+")
    return entry["upgraded_text"] if upgraded else entry["text"]
