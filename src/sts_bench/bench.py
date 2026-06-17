"""Benchmark a suite: baselines and LLM scaffolds over fixed seeds, one command.

    uv run python -m sts_bench.bench --suite smoke --agents random,scripted,floor

Start this first (it listens on the relay port), then trigger
CommunicationMod's external process in-game. Jobs run back to back over the
same game instance: each run starts from the main menu with its job's seed,
plays to game over, and dismisses the death screen so the next can begin.
Logs are grouped one folder per day (logs/<date>/), with protocol logs at the
session root and trajectories, reports, and rendered html each in their own
subfolder. Every job writes its own protocol log and trajectory file; when all
jobs have run, the comparison report lands in logs/<date>/reports/ as markdown
(printed to stdout) and the interactive campaign HTML page in logs/<date>/html/
beside the single-run report rendered for each trajectory, which its run rows
link to.

Baselines (`random`, `scripted`) need no model. LLM scaffolds (`floor`,
`stepwise`) take the same backend selection as sts_bench.play: --model /
--base-url / --api / --reasoning-effort, or the STS_BENCH_* / *_API_KEY env
vars, with .env loaded automatically.

Cost appears in the report only when --pricing names the model: a JSON file
mapping model name to [input, output] dollars per million tokens.

Sessions are composable: every run's trajectory is self-contained, so
baselines recorded one day and model runs another combine into one table
without replaying anything (the report lands under today's date folder):

    uv run python -m sts_bench.bench --report-from logs/*/trajectories/bench-*.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .agents import SCAFFOLDS
from .play import DEFAULT_ENV_FILE
from .providers import PROVIDERS, ProviderError, auto_api
from .report.campaign import CampaignRun, build_campaign_data, render_campaign_html
from .report.model import build_report_data
from .report.page import render_html
from .runner import SUITES, comparison_report
from .runner.async_runner import BASELINES, BenchConfig, run_suite
from .runner.seeds import Suite
from .smoke import run_html_path, session_dir
from .trajectory import read_records


def say(msg: str) -> None:
    print(f"[bench] {msg}", file=sys.stderr)


def _write_reports(
    paths: list[Path],
    suite: Suite | None,
    pricing: dict[str, tuple[float, float]] | None,
) -> tuple[str, Path, Path]:
    """All three report artifacts from one pass over the trajectories.

    The markdown comparison lands in today's `reports/`; every run's
    single-run html page lands in the `html/` folder of its own session
    (beside the trajectory's data, separated from it); the campaign html
    joins the markdown's session and links across to those pages. Html stays
    out of the data and markdown folders, dated runs keep their renderings."""
    today = session_dir()
    report_dir = today / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    campaign_html_dir = today / "html"
    campaign_html_dir.mkdir(parents=True, exist_ok=True)

    campaign_runs = []
    for path in paths:
        records = list(read_records(path))
        page = run_html_path(path)
        page.write_text(render_html(build_report_data(records)), encoding="utf-8")
        href = os.path.relpath(page, campaign_html_dir)
        campaign_runs.append(CampaignRun.from_records(records, page=href))

    report = comparison_report(
        [run.metrics for run in campaign_runs], suite=suite, pricing=pricing
    )
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = suite.name if suite else "combined"
    md_path = report_dir / f"bench-{name}-{stamp}.md"
    md_path.write_text(report, encoding="utf-8")

    data = build_campaign_data(campaign_runs, suite=suite, pricing=pricing)
    html_path = campaign_html_dir / f"bench-{name}-{stamp}.html"
    html_path.write_text(render_campaign_html(data), encoding="utf-8")
    return report, md_path, html_path


def _load_pricing(arg: str | None) -> dict[str, tuple[float, float]] | None:
    if not arg:
        return None
    return {
        model: (rates[0], rates[1])
        for model, rates in json.loads(Path(arg).read_text()).items()
    }


def run(args: argparse.Namespace) -> int:
    if args.report_from:
        # Report-only: no game, no model -- one table over any mix of
        # trajectory files from past sessions.
        suite = SUITES.get(args.suite) if args.suite else None
        if args.suite and suite is None:
            say(f"unknown suite {args.suite!r}: expected {', '.join(SUITES)}")
            return 1
        paths = [Path(p) for p in args.report_from]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            say(f"no such trajectory: {', '.join(missing)}")
            return 1
        report, md_path, html_path = _write_reports(paths, suite, _load_pricing(args.pricing))
        say(f"report over {len(paths)} runs: {md_path}")
        say(f"campaign page: {html_path}")
        print(report)
        return 0

    env_file = Path(args.env_file)
    if env_file.exists():
        applied = [key for key in dotenv_values(env_file) if key not in os.environ]
        load_dotenv(env_file)
        if applied:
            say(f"loaded {', '.join(applied)} from {env_file}")
    elif args.env_file != DEFAULT_ENV_FILE:
        say(f"env file {env_file} not found")
        return 1

    suite = SUITES.get(args.suite or "smoke")
    if suite is None:
        say(f"unknown suite {args.suite!r}: expected {', '.join(SUITES)}")
        return 1
    if args.character or args.ascension is not None:
        # Ad-hoc override, same seeds: the run records carry character and
        # ascension, so these runs still aggregate apart from the suite's own.
        suite = replace(
            suite,
            character=args.character or suite.character,
            ascension=suite.ascension if args.ascension is None else args.ascension,
        )
        say(f"suite {suite.name} overridden: {suite.character} ascension {suite.ascension}")
    agents = [name.strip() for name in args.agents.split(",") if name.strip()]
    known = (*BASELINES, *SCAFFOLDS)
    unknown = [name for name in agents if name not in known]
    if not agents or unknown:
        say(f"unknown agents {unknown!r}: expected any of {', '.join(known)}")
        return 1

    provider = None
    api = None
    if any(name in SCAFFOLDS for name in agents):
        api = args.api or os.environ.get("STS_BENCH_API") or "auto"
        if api == "auto":
            api = auto_api(args.base_url)
        if api not in PROVIDERS:
            say(f"unknown api {api!r}: expected {', '.join(PROVIDERS)} or auto")
            return 1
        provider_kwargs = (
            {"reasoning_effort": args.reasoning_effort} if args.reasoning_effort else {}
        )
        try:
            provider = PROVIDERS[api].from_env(
                model=args.model, base_url=args.base_url, **provider_kwargs
            )
        except ProviderError as exc:
            say(str(exc))
            return 1
        say(f"model: {provider.model} @ {provider.base_url} ({api} api)")

    pricing = _load_pricing(args.pricing)

    config = BenchConfig(
        suite=suite,
        agents=agents,
        provider=provider,
        api=api,
        reasoning_effort=args.reasoning_effort,
        max_rounds=args.max_rounds,
        port=args.port,
        instances=args.instances,
        log_dir=session_dir(),
    )
    results = asyncio.run(run_suite(config, say))

    planned = len(agents) * len(suite.seeds)
    failed = [r for r in results if r.error]
    say(f"jobs: {len(results)} of {planned} attempted, {len(failed)} with errors")
    for result in failed:
        say(f"  {result.agent} {result.seed}: {result.error}")

    paths = [result.trajectory for result in results if result.trajectory.exists()]
    if not paths:
        say("no trajectories recorded; nothing to report")
        return 1
    report, md_path, html_path = _write_reports(paths, suite, pricing)
    say(f"report: {md_path}")
    say(f"campaign page: {html_path}")
    print(report)
    return 0 if len(results) == planned and not failed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--suite",
        default=None,
        help=f"seed suite: {', '.join(SUITES)} (default smoke; with --report-from, "
        "only labels the report header)",
    )
    parser.add_argument(
        "--report-from",
        nargs="+",
        metavar="JSONL",
        default=None,
        help="skip playing; build one comparison report over these trajectory "
        "files (any mix of past bench/play runs)",
    )
    parser.add_argument(
        "--agents",
        default="random,scripted,floor",
        help="comma list, run in order: random, scripted (baselines), "
        "floor, stepwise (LLM scaffolds)",
    )
    parser.add_argument(
        "--character",
        default=None,
        help="override the suite's character (e.g. silent); seeds stay the suite's",
    )
    parser.add_argument(
        "--ascension", type=int, default=None, help="override the suite's ascension"
    )
    parser.add_argument("--model", default=None, help="model name (default: from env)")
    parser.add_argument("--base-url", default=None, help="chat-completions base URL (default: from env)")
    parser.add_argument(
        "--api",
        choices=("auto", "chat", "responses", "anthropic"),
        default=None,
        help="wire format for LLM agents (see sts_bench.play --help)",
    )
    parser.add_argument("--reasoning-effort", default=None, help="model deliberation knob (see sts_bench.play --help)")
    parser.add_argument("--max-rounds", type=int, default=10, help="provider calls per decision point")
    parser.add_argument("--instances", type=int, default=1, help="connected game instances (workers)")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--pricing", default=None, help="JSON file: model -> [input, output] $ per Mtok")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="env file with keys/config (real env vars win)")
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
