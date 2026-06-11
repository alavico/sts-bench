"""Re-render logged game states through the current serializer.

Iterating on the digest needs a fast loop from "state the game actually sent"
to "text the model would see now". This replays the `<<` wire lines of a
protocol log -- or a single raw state JSON (a fixture, an unparsed capture) --
through parse + cursory_view, so a serializer change can be checked against
real states without launching the game. States the schema rejects render as
their parse error instead of crashing: those are the same states play.py
would have captured to logs/unparsed/.

    uv run python -m sts_bench.render logs/latest.log
    uv run python -m sts_bench.render logs/latest.log --screen COMBAT_REWARD --deck
    uv run python -m sts_bench.render logs/latest.log --index 142
    uv run python -m sts_bench.render tests/fixtures/states/shop_screen-1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from .state import StateParseError, parse_message
from .state.serialize import combat_briefing, cursory_view

WIRE_TAG = " << "


def iter_states(path: Path) -> Iterator[dict]:
    """Game states from a protocol log (one per `<<` wire line) or a raw JSON file."""
    if path.suffix == ".json":
        yield json.loads(path.read_text(encoding="utf-8"))
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            _, sep, payload = line.partition(WIRE_TAG)
            if not sep:
                continue
            message = json.loads(payload)
            if "error" in message:
                continue  # command rejections carry no new state
            yield message


def render_states(
    states: Iterable[dict],
    screen: str | None = None,
    floor: int | None = None,
    index: int | None = None,
    deck: bool = False,
) -> Iterator[str]:
    """One text block per state: a locator header, then the model-facing digest.

    States are numbered before filtering, so `--index N` always means the same
    state regardless of which filters found it.
    """
    for n, message in enumerate(states, 1):
        game_state = message.get("game_state") or {}
        if screen is not None and game_state.get("screen_type") != screen:
            continue
        if floor is not None and game_state.get("floor") != floor:
            continue
        if index is not None and n != index:
            continue
        ready = "" if message.get("ready_for_command", True) else " | not ready"
        header = (
            f"--- state {n} | floor {game_state.get('floor')} | "
            f"{game_state.get('screen_type')}{ready} ---"
        )
        try:
            parsed = parse_message(message)
        except StateParseError as exc:
            yield f"{header}\nUNPARSED: {str(exc)[:300]}"
            continue
        body = cursory_view(parsed)
        if deck and parsed.game_state is not None and parsed.game_state.combat_state is not None:
            body = f"{combat_briefing(parsed.game_state)}\n{body}"
        yield f"{header}\n{body}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="protocol log, or a raw state JSON file")
    parser.add_argument("--screen", default=None, help="only states with this screen_type (e.g. COMBAT_REWARD)")
    parser.add_argument("--floor", type=int, default=None, help="only states on this floor")
    parser.add_argument("--index", type=int, default=None, help="only the Nth state of the log")
    parser.add_argument("--deck", action="store_true", help="prepend the combat briefing (relic bar + deck text)")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        raise SystemExit(1)
    for block in render_states(
        iter_states(path), screen=args.screen, floor=args.floor, index=args.index, deck=args.deck
    ):
        print(block, end="\n\n")


if __name__ == "__main__":
    main()
