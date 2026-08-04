"""Job-queue runner: a list of fully-specified runs, played back to back.

One harness server; every connected game instance becomes a worker pulling
jobs off a shared queue, so scaling to more instances is a count, not a new
runner. v1 runs one instance -- strictly sequential -- but the seams are
already async.

A job is one full run, and carries everything that identifies it: agent,
model provider, reasoning effort, character, ascension, seed. Queued jobs
need not share anything -- a cheap model at ascension 0 can be followed by a
frontier model at ascension 10. From the main menu, `start` with the job's
seed, play to game over, dismiss the death screen back to the menu, where
the next job begins. Each job gets its own protocol log and trajectory file
(paired by name, exactly as a single `play` run writes them), so a failed
job costs exactly one run -- unless the game itself is left wedged mid-run,
in which case the worker stops rather than corrupt every following job.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..agents import SCAFFOLDS, RandomAgent, ScriptedAgent
from ..env import CommunicationModEnv, HarnessServer
from ..protocol_log import ProtocolLog
from ..providers import ModelProvider
from ..smoke import LOG_DIR
from ..tools import ToolRegistry
from ..trajectory import RunRecorder, TrajectoryStore
from .session import RunTally, play_run

BASELINES = ("random", "scripted")
Say = Callable[[str], None]


@dataclass
class JobSpec:
    """One queued run, self-contained: who plays, and which game they play."""

    agent: str  # a baseline name or a SCAFFOLDS key
    seed: str
    character: str = "ironclad"
    ascension: int = 0
    provider: ModelProvider | None = None  # required when the agent is a scaffold
    api: str | None = None
    reasoning_effort: str | None = None
    max_rounds: int = 10

    @property
    def name(self) -> str:
        """How the job reads in narration and summaries."""
        who = self.provider.model if self.provider is not None else self.agent
        effort = f" effort={self.reasoning_effort}" if self.reasoning_effort else ""
        return f"{who}{effort} a{self.ascension} {self.seed}"


@dataclass
class QueueConfig:
    jobs: list[JobSpec] = field(default_factory=list)
    port: int = 9999
    instances: int = 1
    log_dir: Path = LOG_DIR
    accept_timeout: float = 300.0


@dataclass
class JobResult:
    job: JobSpec
    run_id: str
    trajectory: Path
    error: str | None = None
    # The game can no longer start a fresh run (wedged wire or stuck
    # mid-run); the worker that hit this stopped taking jobs.
    fatal: bool = False


@dataclass(frozen=True)
class AgentIdentity:
    """What the run record says about who played."""

    model: str
    base_url: str
    api: str
    prompt_hash: str | None
    tool_schema_hash: str | None
    reasoning_effort: str | None


class LogRouter:
    """One env, many per-run logs.

    The env's protocol-line hook is fixed at construction, but each job owns
    its own logfile; this forwards wire lines to whichever job's log is
    current (and drops between-job stragglers).
    """

    def __init__(self):
        self._log: ProtocolLog | None = None

    def attach(self, log: ProtocolLog) -> None:
        self._log = log

    def detach(self) -> None:
        self._log = None

    def line(self, tag: str, text: str) -> None:
        if self._log is not None:
            self._log.line(tag, text)


async def run_jobs(
    config: QueueConfig, say: Say, server: HarnessServer | None = None
) -> list[JobResult]:
    """Run the queued jobs in order; returns one JobResult per job attempted."""
    jobs: asyncio.Queue[JobSpec] = asyncio.Queue()
    for job in config.jobs:
        jobs.put_nowait(job)
    results: list[JobResult] = []

    async def drive(srv: HarnessServer) -> None:
        say(
            f"{jobs.qsize()} jobs on {config.instances} game instance(s); "
            f"listening on {srv.host}:{srv.port} -- start the external process in-game"
        )
        await asyncio.gather(
            *(
                _worker(index, srv, jobs, config, results, say)
                for index in range(config.instances)
            )
        )

    if server is not None:
        await drive(server)
    else:
        with HarnessServer(port=config.port) as own_server:
            await drive(own_server)
    return results


async def _worker(
    index: int,
    server: HarnessServer,
    jobs: asyncio.Queue,
    config: QueueConfig,
    results: list[JobResult],
    say: Say,
) -> None:
    conn = await asyncio.to_thread(server.accept, timeout=config.accept_timeout)
    say(f"game {index}: relay connected from {conn.peer}")
    router = LogRouter()
    env = CommunicationModEnv(conn, on_protocol_line=router.line)
    state = await asyncio.to_thread(env.handshake)
    if state.get("in_game"):
        say(f"game {index}: already mid-run; return to the main menu and rerun")
        return
    while True:
        try:
            job = jobs.get_nowait()
        except asyncio.QueueEmpty:
            return
        result = await asyncio.to_thread(_run_job, env, router, job, config, say)
        results.append(result)
        if result.fatal:
            say(
                f"game {index}: cannot start another run ({result.error}); "
                "abandoning this instance's remaining jobs"
            )
            return


def _run_job(
    env: CommunicationModEnv,
    router: LogRouter,
    job: JobSpec,
    config: QueueConfig,
    say: Say,
) -> JobResult:
    # Queued runs are ordinary runs: same log/trajectory names as a manual
    # `play` session, with the job's identity carried by the run record, not
    # the filename.
    log = ProtocolLog(config.log_dir, name="play")
    router.attach(log)
    run_id = log.path.stem

    def narrate(msg: str) -> None:
        say(f"[{job.name}] {msg}")
        log.line("--", msg)

    tally = RunTally()
    store: TrajectoryStore | None = None
    recorder: RunRecorder | None = None
    error: str | None = None
    fatal = False
    try:
        agent, system_prompt, identity = _build_agent(job)
        effort_note = f", effort {identity.reasoning_effort}" if identity.reasoning_effort else ""
        log.line(
            "--",
            f"model: {identity.model} @ {identity.base_url} ({identity.api} api{effort_note}) | "
            f"{job.agent} agent | {job.character} ascension {job.ascension} seed {job.seed}",
        )
        if system_prompt is not None:
            log.line("--", "system prompt (constant; logged once):")
            for prompt_line in system_prompt.splitlines():
                log.line(">m", prompt_line)
        store = TrajectoryStore(config.log_dir / "trajectories", run_id=run_id)
        recorder = RunRecorder(
            store,
            run_id=run_id,
            seed=job.seed,
            character=job.character,
            ascension=job.ascension,
            model=identity.model,
            provider_base_url=identity.base_url,
            api=identity.api,
            reasoning_effort=identity.reasoning_effort,
            agent=job.agent,
            prompt_hash=identity.prompt_hash,
            tool_schema_hash=identity.tool_schema_hash,
        )
        narrate(f"trajectory: {store.path}")
        play_run(
            env,
            env.state,
            agent,
            recorder,
            character=job.character,
            ascension=job.ascension,
            seed=job.seed,
            log=log,
            say=narrate,
            tally=tally,
        )
        error = tally.aborted
        fatal = tally.fatal
        if not fatal and (tally.end_state or {}).get("in_game"):
            error = error or "run stopped while still in game"
            fatal = True
    except Exception as exc:  # one bad job must not take the queue down silently
        error = f"{type(exc).__name__}: {exc}"
        # If the game is still mid-run (or the wire is gone), the next job
        # cannot start; only a clean menu state lets the worker continue.
        try:
            fatal = bool(env.state.get("in_game"))
        except Exception:
            fatal = True
        narrate(f"job failed: {error}")
    finally:
        if recorder is not None:
            recorder.finish()
        if store is not None:
            store.close()
        narrate(
            f"job done: {tally.decisions} decisions, {tally.forced} forced, "
            f"{tally.unparsed} unparsed"
            + (f", ERROR: {error}" if error else "")
        )
        router.detach()
        log.close()
    return JobResult(
        job=job,
        run_id=run_id,
        trajectory=config.log_dir / "trajectories" / f"{run_id}.jsonl",
        error=error,
        fatal=fatal,
    )


def _build_agent(job: JobSpec) -> tuple[object, str | None, AgentIdentity]:
    """The agent, its system prompt (None for baselines), and its identity."""
    if job.agent == "random":
        # Seed the pick sequence with the run seed: same game, same walk.
        return (
            RandomAgent(rng_seed=job.seed),
            None,
            AgentIdentity("random", "builtin", "none", None, None, None),
        )
    if job.agent == "scripted":
        return (
            ScriptedAgent(),
            None,
            AgentIdentity("scripted", "builtin", "none", None, None, None),
        )
    cls, prompt_template = SCAFFOLDS[job.agent]
    provider = job.provider
    if provider is None:
        raise ValueError(f"agent {job.agent!r} needs a model provider; none configured")
    system_prompt = prompt_template.format(character=job.character.upper())
    identity = AgentIdentity(
        model=provider.model,
        base_url=provider.base_url,
        api=job.api or "chat",
        prompt_hash=hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16],
        tool_schema_hash=hashlib.sha256(
            json.dumps(ToolRegistry().openai_tools(), sort_keys=True).encode("utf-8")
        ).hexdigest()[:16],
        reasoning_effort=job.reasoning_effort,
    )
    return cls(provider, max_rounds=job.max_rounds), system_prompt, identity
