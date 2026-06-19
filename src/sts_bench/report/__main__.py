"""Build a self-contained HTML report of one run from its trajectory JSONL.

The page embeds everything: HP and spend charts across the floors, the route
walked on each act map, turn-by-turn tables for every fight, and each
decision's full conversation slice -- the same packets replay renders, made
browsable. One file, opens offline.

    uv run python -m sts_bench.report logs/2026-06-11/trajectories/play-20260611-165404.jsonl
    uv run python -m sts_bench.report <file> -o report.html

The default output is the `html/` folder of the trajectory's own session.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..smoke import run_html_path
from ..trajectory import read_records
from .model import build_report_data
from .page import render_html


def _load_pricing(arg: str | None) -> dict[str, tuple[float, float]] | None:
    """Pricing from a JSON file (model -> [input, output] $/Mtok). Defaults to
    costs.json in the working directory; absent file just means no cost line."""
    path = Path(arg) if arg else Path("costs.json")
    if not path.exists():
        return None
    return {m: (r[0], r[1]) for m, r in json.loads(path.read_text()).items()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("path", help="trajectory JSONL file (logs/<date>/trajectories/*.jsonl)")
    parser.add_argument(
        "-o", "--out", default=None, help="output HTML path (default: the session's html/ folder)"
    )
    parser.add_argument(
        "--pricing", default=None,
        help="JSON file: model -> [input, output] $ per Mtok (default: costs.json if present)",
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        raise SystemExit(1)

    data = build_report_data(list(read_records(path)), _load_pricing(args.pricing))
    out = Path(args.out) if args.out else run_html_path(path)
    out.write_text(render_html(data))
    decisions = sum(len(floor["decisions"]) for floor in data["floors"])
    print(f"{out} ({len(data['floors'])} floors, {decisions} decisions)")


if __name__ == "__main__":
    main()
