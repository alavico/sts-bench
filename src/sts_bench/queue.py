"""Queue runs from a file and play them back to back, unattended.

    uv run python -m sts_bench.queue runs.txt

The file lists one run per line, using the same flags as sts_bench.play
(blank lines and `#` comments are skipped):

    # GPT-5.6 sweep, medium effort
    --model gpt-5.6-luna --api responses --reasoning-effort medium --seed STSBENCH1
    --model gpt-5.6-luna --api responses --reasoning-effort medium --seed STSBENCH2
    --model gpt-5.6-terra --api responses --reasoning-effort medium --ascension 5 --seed STSBENCH1
    --agent random --seed STSBENCH1

Start this first (it listens on the relay port), then trigger
CommunicationMod's external process in-game once. Jobs run back to back over
the same game instance: each starts from the main menu with its seed, plays
to game over, and dismisses the death screen so the next can begin. Every
job's provider is built -- key resolved, backend named -- before the first
run starts, so a typo on line 7 surfaces immediately, not an hour in.

Each job writes the same protocol log and trajectory a manual play session
would; there is no queue-level artifact. Reports stay a separate step over
whatever trajectories you point them at:

    uv run python -m sts_bench.bench --report-from logs/*/trajectories/play-*.jsonl --pricing costs.json
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import sys
from pathlib import Path

from .agents import SCAFFOLDS
from .cli import add_character_ascension_args, add_env_file_arg, apply_env_file
from .providers import PROVIDERS, ProviderError, auto_api
from .runner.async_runner import BASELINES, JobSpec, QueueConfig, run_jobs
from .runner.metrics import RunMetrics
from .smoke import session_dir


def say(msg: str) -> None:
    print(f"[queue] {msg}", file=sys.stderr)


def _job_parser() -> argparse.ArgumentParser:
    """The per-line flags: sts_bench.play's run-defining arguments."""
    parser = argparse.ArgumentParser(prog="<job line>", add_help=False, exit_on_error=False)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api", choices=("auto", *PROVIDERS), default=None)
    parser.add_argument("--agent", choices=(*BASELINES, *SCAFFOLDS), default="floor")
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--seed", default="STSBENCH1")
    add_character_ascension_args(parser)
    parser.add_argument("--max-rounds", type=int, default=10)
    return parser


def parse_jobs(text: str) -> list[JobSpec]:
    """Every line becomes a fully-built job, provider and all, before anything
    runs -- a bad model name or missing key must fail the whole queue up front,
    not strand it halfway through an unattended session."""
    parser = _job_parser()
    jobs: list[JobSpec] = []
    problems: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            spec, unknown = parser.parse_known_args(shlex.split(line))
        except argparse.ArgumentError as exc:
            problems.append(f"line {lineno}: {exc}")
            continue
        if unknown:
            problems.append(f"line {lineno}: unknown flags {' '.join(unknown)}")
            continue

        provider = None
        api = None
        if spec.agent in SCAFFOLDS:
            api = spec.api or os.environ.get("STS_BENCH_API") or "auto"
            if api == "auto":
                api = auto_api(spec.base_url)
            if api not in PROVIDERS:
                problems.append(f"line {lineno}: unknown api {api!r}")
                continue
            kwargs = {"reasoning_effort": spec.reasoning_effort} if spec.reasoning_effort else {}
            try:
                provider = PROVIDERS[api].from_env(
                    model=spec.model, base_url=spec.base_url, **kwargs
                )
            except ProviderError as exc:
                problems.append(f"line {lineno}: {exc}")
                continue
        jobs.append(
            JobSpec(
                agent=spec.agent,
                seed=spec.seed,
                character=spec.character,
                ascension=spec.ascension,
                provider=provider,
                api=api,
                reasoning_effort=spec.reasoning_effort,
                max_rounds=spec.max_rounds,
            )
        )
    if problems:
        raise ValueError("; ".join(problems))
    return jobs


def _summary_line(result) -> str:
    if not result.trajectory.exists():
        return f"{result.job.name}: no trajectory ({result.error or 'unknown error'})"
    m = RunMetrics.from_file(result.trajectory)
    if m.victory:
        outcome = f"VICTORY floor {m.floor_reached}"
    elif m.victory is False:
        outcome = f"DEFEAT floor {m.floor_reached}"
    else:
        outcome = f"UNFINISHED floor {m.floor_reached}"
    score = f", score {m.score}" if m.score is not None else ""
    note = f", ERROR: {result.error}" if result.error else ""
    return f"{result.job.name}: {outcome}{score}, {m.decisions} decisions{note} -> {result.trajectory}"


def run(args: argparse.Namespace) -> int:
    if not apply_env_file(args.env_file, say):
        return 1
    path = Path(args.jobs)
    if not path.exists():
        say(f"no such jobs file: {path}")
        return 1
    try:
        jobs = parse_jobs(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        say(f"bad jobs file: {exc}")
        return 1
    if not jobs:
        say(f"{path} defines no jobs")
        return 1
    say(f"{len(jobs)} jobs queued:")
    for job in jobs:
        say(f"  {job.name}")

    config = QueueConfig(
        jobs=jobs,
        port=args.port,
        instances=args.instances,
        log_dir=session_dir(),
    )
    results = asyncio.run(run_jobs(config, say))

    failed = [r for r in results if r.error]
    say(f"jobs: {len(results)} of {len(jobs)} attempted, {len(failed)} with errors")
    for result in results:
        say(f"  {_summary_line(result)}")
    return 0 if len(results) == len(jobs) and not failed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("jobs", help="file with one run per line (play-style flags)")
    parser.add_argument("--instances", type=int, default=1, help="connected game instances (workers)")
    parser.add_argument("--port", type=int, default=9999)
    add_env_file_arg(parser)
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
