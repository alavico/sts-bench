"""Static power text: the tooltip a player reads when hovering a buff/debuff.

Powers were the last entity without a pinned database, and the gap had two
costs observed live: a bare `Hex 1` made the model fall back on pretrained
game knowledge (a parity violation -- the human reads the tooltip), and the
wire's `-1` no-stacks sentinel rendered literally (`Split -1`), unreadable
apart from a genuine negative stack like `Strength -1`.

The snapshot comes from the same spire-archive parse as the other databases
(data/sts1/powers.json, trimmed to id/name/description/stackable); twenty
descriptions whose literal numbers the upstream parse mangled into `X<digit>`
were corrected by hand at trim time, values verified against the game.
Descriptions keep `X` as the placeholder for the power's live amount -- the
power line in the combat view carries the current number, the same division
of labor as card text (base printing) vs the combat section (live buffs).

`stackable` here means "amounts add when reapplied", *not* "the amount is
meaningful": Vulnerable is non-stackable yet its amount is its remaining
turns. So the amount is dropped from rendering only when it is the `-1`
sentinel on a power known to be non-stackable -- never on mere
non-stackability, and never on unknown powers.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from ..state.schema import Power
from .card_db import _squash

DATA_PATH = Path(__file__).parent / "data" / "sts1_powers.json"


@cache
def _index() -> tuple[dict[str, dict], dict[str, dict]]:
    entries = json.loads(DATA_PATH.read_text())
    by_id: dict[str, dict] = {}
    for entry in entries:
        by_id.setdefault(entry["id"], entry)
        by_id.setdefault(_squash(entry["id"]), entry)
    by_name = {entry["name"].lower(): entry for entry in entries}
    return by_id, by_name


def _lookup(power: Power) -> dict | None:
    by_id, by_name = _index()
    return (
        by_id.get(power.id.upper().replace(" ", "_"))
        or by_id.get(_squash(power.id))
        or by_name.get(power.name.lower())
    )


def power_text(power: Power) -> str | None:
    """Tooltip text for a power as the mod reports it; None when unknown.
    `X` in the text stands for the power's current amount."""
    entry = _lookup(power)
    return entry["description"] if entry else None


def amount_is_sentinel(power: Power) -> bool:
    """True when the wire's amount is the -1 placeholder, not a real value.

    Only claimed for powers the database knows to be non-stackable; a `-1`
    on a stackable power (Strength) is a genuine negative stack, and a `-1`
    on an unknown power stays visible rather than silently vanishing.
    """
    if power.amount != -1:
        return False
    entry = _lookup(power)
    return entry is not None and not entry["stackable"]
