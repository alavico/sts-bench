"""Harvest state fixtures from a protocol log.

    uv run python -m sts_bench.harvest logs/latest.log

Reads every inbound (`<<`) JSON message from a session log and keeps only the
messages that show the parser something *structurally* new: a state is stored
iff it contributes (key-path, type) pairs not already covered by the existing
fixtures in tests/fixtures/states/. Two combat turns with different cards but
the same shape are one fixture; a turn where a monster first has powers is a
new one. Coverage therefore only grows when a session genuinely reaches new
ground, and most harvests are no-ops.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "states"
# Matches both ProtocolLog ("<<") and the legacy main.py tee ("<< IN ") formats.
INBOUND = re.compile(r"^\S+ <<\s*(?:IN\s+)?(\{.*)$")

Pair = tuple[str, str]


def inbound_messages(log_text: str):
    for line in log_text.splitlines():
        match = INBOUND.match(line)
        if not match:
            continue
        try:
            yield json.loads(match.group(1))
        except json.JSONDecodeError:
            continue


def key_for(message: dict) -> str:
    if "error" in message:
        return "error"
    if not message.get("in_game"):
        return "out_of_game"
    game_state = message.get("game_state") or {}
    return str(game_state.get("screen_type", "unknown")).lower()


def signature(message: dict) -> set[Pair]:
    """All (key-path, value-type) pairs in the message, prefixed by its screen
    key so every screen type keeps at least one fixture even when its shape
    overlaps another screen's."""
    prefix = key_for(message)
    pairs: set[Pair] = set()

    def walk(value, path: str) -> None:
        if isinstance(value, dict):
            pairs.add((path, "object"))
            for k, v in value.items():
                walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            pairs.add((path, "array"))
            for item in value:
                walk(item, f"{path}[]")
        elif isinstance(value, bool):
            pairs.add((path, "bool"))
        elif isinstance(value, int):
            pairs.add((path, "int"))
        elif isinstance(value, float):
            pairs.add((path, "float"))
        elif value is None:
            pairs.add((path, "null"))
        else:
            pairs.add((path, "str"))

    walk(message, prefix)
    return pairs


def harvest(log_path: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    covered: set[Pair] = set()
    counts: dict[str, int] = defaultdict(int)
    for existing in sorted(out_dir.glob("*.json")):
        covered |= signature(json.loads(existing.read_text()))
        key, _, _ = existing.stem.rpartition("-")
        counts[key] += 1

    written = []
    for message in inbound_messages(log_path.read_text(encoding="utf-8")):
        pairs = signature(message)
        if pairs <= covered:
            continue
        covered |= pairs
        key = key_for(message)
        counts[key] += 1
        path = out_dir / f"{key}-{counts[key]}.json"
        path.write_text(json.dumps(message, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path, help="protocol log to harvest")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="fixture directory")
    args = parser.parse_args()

    written = harvest(args.log, args.out)
    if not written:
        print("no new states (everything already covered)", file=sys.stderr)
    for path in written:
        print(path)


if __name__ == "__main__":
    main()
