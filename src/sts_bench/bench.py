"""Build the comparison reports over recorded trajectories.

    uv run python -m sts_bench.bench --report-from logs/*/trajectories/*.jsonl --pricing costs.json

No game, no model: runs are sessions' artifacts and the report is a view.
Any mix of trajectory files from any sessions combines into one table --
baselines recorded one day and model runs another aggregate exactly as if
they had run together, and runs whose prompt or tool schemas differ are
never averaged (the report keeps them in separate rows and says why).

Three artifacts per invocation: the markdown comparison in today's
`logs/<date>/reports/` (also printed to stdout), a single-run HTML page for
every trajectory in its own session's `html/` folder, and the consolidated
campaign page at logs/campaign.html -- a fixed spot the next invocation
overwrites, its run rows linking down to those per-run pages.

Cost appears only when --pricing names the model: a JSON file mapping model
name to [input, output] dollars per million tokens.

To record new runs, use sts_bench.play (one run) or sts_bench.queue (many,
back to back).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from .report.campaign import CampaignRun, build_campaign_data, render_campaign_html
from .report.model import build_report_data
from .report.page import render_html
from .runner import comparison_report
from .smoke import CAMPAIGN_HTML, run_html_path, session_dir
from .trajectory import read_records


# These prompt-development pilots are useful evidence for the technical write-up,
# but they predate the frozen benchmark surface and are not benchmark observations.
# Keep the trajectories and their existing run pages; exclude them only from the
# consolidated public report and its aggregates.
PUBLIC_REPORT_EXCLUSIONS = {
    "play-20260611-165404": "preliminary prompt v1",
    "bench-floor-STSBENCH1-20260612-151309": "preliminary prompt v2",
    "bench-floor-STSBENCH2-20260612-152701": "preliminary prompt v2",
}


def say(msg: str) -> None:
    print(f"[bench] {msg}", file=sys.stderr)


def _write_reports(
    paths: list[Path],
    pricing: dict[str, tuple[float, float]] | None,
) -> tuple[str, Path, Path, int, int]:
    """All three report artifacts from one pass over the trajectories.

    The markdown comparison lands in today's `reports/`; every run's
    single-run html page lands in the `html/` folder of its own session
    (beside the trajectory's data, separated from it); the campaign html is
    the one consolidated view over all of them, so it lives at a fixed spot
    at the logs root and is overwritten each run rather than dated. It links
    across to the run pages, and each of them links back up."""
    today = session_dir()
    report_dir = today / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    # The campaign page's path is fixed up front so every run page can carry
    # a link back to it; being date-independent, a regeneration replaces the
    # previous consolidated report instead of adding another beside it.
    html_path = CAMPAIGN_HTML
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    included_paths = [path for path in paths if path.stem not in PUBLIC_REPORT_EXCLUSIONS]
    excluded_runs = [
        {"run_id": path.stem, "reason": PUBLIC_REPORT_EXCLUSIONS[path.stem]}
        for path in paths
        if path.stem in PUBLIC_REPORT_EXCLUSIONS
    ]

    campaign_runs = []
    for path in included_paths:
        records = list(read_records(path))
        page = run_html_path(path)
        data = build_report_data(
            records, pricing, campaign=os.path.relpath(html_path, page.parent)
        )
        page.write_text(render_html(data), encoding="utf-8")
        href = os.path.relpath(page, html_path.parent)
        campaign_runs.append(CampaignRun.from_records(records, page=href))

    report = comparison_report([run.metrics for run in campaign_runs], pricing=pricing)
    md_path = report_dir / f"bench-report-{stamp}.md"
    md_path.write_text(report, encoding="utf-8")

    data = build_campaign_data(
        campaign_runs,
        pricing=pricing,
        excluded_runs=excluded_runs,
    )
    html_path.write_text(render_campaign_html(data), encoding="utf-8")
    return report, md_path, html_path, len(campaign_runs), len(excluded_runs)


def _load_pricing(arg: str | None) -> dict[str, tuple[float, float]] | None:
    if not arg:
        return None
    return {
        model: (rates[0], rates[1])
        for model, rates in json.loads(Path(arg).read_text()).items()
    }


def run(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.report_from]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        say(f"no such trajectory: {', '.join(missing)}")
        return 1
    report, md_path, html_path, included, excluded = _write_reports(
        paths, _load_pricing(args.pricing)
    )
    suffix = f" ({excluded} pilot runs excluded)" if excluded else ""
    say(f"report over {included} runs{suffix}: {md_path}")
    say(f"campaign page: {html_path}")
    print(report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--report-from",
        nargs="+",
        required=True,
        metavar="JSONL",
        help="trajectory files to report over (any mix of past runs)",
    )
    parser.add_argument("--pricing", default=None, help="JSON file: model -> [input, output] $ per Mtok")
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
