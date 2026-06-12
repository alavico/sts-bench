"""Keyword definitions: the tooltip boxes next to a card.

Card text leans on game vocabulary ("Apply 2 Vulnerable. Exhaust.") that the
game itself always defines -- keyword tooltips appear beside any card the
player is reading, on the same hover that reads the text. Without them the
exact effects are a memory test. This module matches keywords inside rules
text so digests can append definitions for exactly the terms on screen --
never a full glossary of mechanics the run doesn't use.

Source: https://github.com/nkhoit/spire-archive, data/sts1/keywords.json
(trimmed to id/names/description; dev-only TODO/UNKNOWN entries dropped).
Re-pin alongside the cards snapshot if the game version moves.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Iterable

DATA_PATH = Path(__file__).parent / "data" / "sts1_keywords.json"


@cache
def _matcher() -> tuple[re.Pattern, dict[str, dict]]:
    entries = json.loads(DATA_PATH.read_text())
    by_name: dict[str, dict] = {}
    for entry in entries:
        for name in entry["names"]:
            by_name[name.lower()] = entry
    # Longest names first so "through violence" wins over a bare "violence";
    # lookarounds instead of \b because names can end in '+' or contain '-'.
    alternation = "|".join(re.escape(name) for name in sorted(by_name, key=len, reverse=True))
    pattern = re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9+])", re.IGNORECASE)
    return pattern, by_name


def keyword_lines(texts: Iterable[str | None]) -> list[str]:
    """`Name: definition` for every keyword found in the given rules texts,
    in order of first appearance, each defined once."""
    pattern, by_name = _matcher()
    seen: set[str] = set()
    lines: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in pattern.finditer(text):
            entry = by_name[match.group(0).lower()]
            if entry["id"] not in seen:
                seen.add(entry["id"])
                lines.append(f"{entry['names'][0].capitalize()}: {entry['description']}")
    return lines
