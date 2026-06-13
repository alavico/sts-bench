"""Seed suites: the fixed ground every comparison stands on.

A suite names the exact seeds, character, and ascension a configuration is
scored over -- runs on different suites never average together. Suites are
data, so growing from smoke (5 seeds) toward dev/benchmark tiers is an
addition here, not a code change.

Seeds are the game's alphanumeric seed strings (the game's own alphabet has
no letter O, so none appears here).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Suite:
    name: str
    character: str
    ascension: int
    seeds: tuple[str, ...]


SUITES: dict[str, Suite] = {
    "smoke": Suite(
        name="smoke",
        character="ironclad",
        ascension=0,
        # STSBENCH1 is the seed every milestone's acceptance runs used; the
        # rest of the suite numbers on from it.
        seeds=("STSBENCH1", "STSBENCH2", "STSBENCH3", "STSBENCH4", "STSBENCH5"),
    ),
    # Same seeds, different class: a seed fixes the run's generation, so
    # sharing them isolates the character as the only changed variable.
    "smoke-silent": Suite(
        name="smoke-silent",
        character="silent",
        ascension=0,
        seeds=("STSBENCH1", "STSBENCH2", "STSBENCH3", "STSBENCH4", "STSBENCH5"),
    ),
}
