"""Suite runner: every (agent, seed) job of a benchmark suite, back to back.

One harness server; every connected game instance becomes a worker pulling
jobs off a shared queue, so scaling to more instances is a count, not a new
runner. v1 runs one instance -- strictly sequential -- but the seams are
already async.

A job is one full run: from the main menu, `start` with the job's seed, play
to game over, dismiss the death screen back to the menu, where the next job
begins. Each job gets its own protocol log and trajectory file (paired by
name), so a failed job costs exactly one run -- unless the game itself is
left wedged mid-run, in which case the worker stops rather than corrupt
every following job.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..agents import SCAFFOLDS, RandomAgent, ScriptedAgent
from ..env import CommunicationModEnv, HarnessServer
from ..protocol_log import ProtocolLog
from ..providers import ModelProvider
from ..smoke import LOG_DIR
from ..tools import ToolRegistry
from ..trajectory import RunRecorder, TrajectoryStore
from .seeds import Suite
from .session import RunTally, play_run

BASELINES = ("random", "scripted")
Say = Callable[[str], None]


@dataclass
class BenchConfig:
    suite: Suite
    agents: list[str]  # baseline names and/or SCAFFOLDS keys, in run order
    provider: ModelProvider | None = None  # required when any scaffold runs
    api: str | None = None
    reasoning_effort: str | None = None
    max_rounds: int = 10
    port: int = 9999
    instances: int = 1
    log_dir: Path = LOG_DIR
    accept_timeout: float = 300.0


@dataclass
class JobResult:
    agent: str
    seed: str
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


async def run_suite(
    config: BenchConfig, say: Say, server: HarnessServer | None = None
) -> list[JobResult]:
    """Run agents x seeds; returns one JobResult per job attempted."""
    jobs: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    for agent in config.agents:
        for seed in config.suite.seeds:
            jobs.put_nowait((agent, seed))
    results: list[JobResult] = []

    async def drive(srv: HarnessServer) -> None:
        say(
            f"{jobs.qsize()} jobs ({', '.join(config.agents)} x "
            f"{len(config.suite.seeds)} seeds) on {config.instances} game instance(s); "
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
    config: BenchConfig,
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
            agent_name, seed = jobs.get_nowait()
        except asyncio.QueueEmpty:
            return
        result = await asyncio.to_thread(
            _run_job, env, router, agent_name, seed, config, say
        )
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
    agent_name: str,
    seed: str,
    config: BenchConfig,
    say: Say,
) -> JobResult:
    suite = config.suite
    log = ProtocolLog(config.log_dir, name=f"bench-{agent_name}-{seed}")
    router.attach(log)
    run_id = log.path.stem

    def narrate(msg: str) -> None:
        say(f"[{agent_name} {seed}] {msg}")
        log.line("--", msg)

    tally = RunTally()
    store: TrajectoryStore | None = None
    recorder: RunRecorder | None = None
    error: str | None = None
    fatal = False
    try:
        agent, system_prompt, identity = _build_agent(agent_name, seed, config)
        log.line(
            "--",
            f"bench job: {identity.model} ({identity.api} api) | {agent_name} agent | "
            f"{suite.character} ascension {suite.ascension} seed {seed} | suite {suite.name}",
        )
        if system_prompt is not None:
            log.line("--", "system prompt (constant; logged once):")
            for prompt_line in system_prompt.splitlines():
                log.line(">m", prompt_line)
        store = TrajectoryStore(config.log_dir / "trajectories", run_id=run_id)
        recorder = RunRecorder(
            store,
            run_id=run_id,
            seed=seed,
            character=suite.character,
            ascension=suite.ascension,
            model=identity.model,
            provider_base_url=identity.base_url,
            api=identity.api,
            reasoning_effort=identity.reasoning_effort,
            agent=agent_name,
            prompt_hash=identity.prompt_hash,
            tool_schema_hash=identity.tool_schema_hash,
        )
        narrate(f"trajectory: {store.path}")
        play_run(
            env,
            env.state,
            agent,
            recorder,
            character=suite.character,
            ascension=suite.ascension,
            seed=seed,
            log=log,
            say=narrate,
            tally=tally,
        )
        error = tally.aborted
        fatal = tally.fatal
        if not fatal and (tally.end_state or {}).get("in_game"):
            error = error or "run stopped while still in game"
            fatal = True
    except Exception as exc:  # one bad job must not take the suite down silently
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
        agent=agent_name,
        seed=seed,
        run_id=run_id,
        trajectory=config.log_dir / "trajectories" / f"{run_id}.jsonl",
        error=error,
        fatal=fatal,
    )


def _build_agent(
    agent_name: str, seed: str, config: BenchConfig
) -> tuple[object, str | None, AgentIdentity]:
    """The agent, its system prompt (None for baselines), and its identity."""
    if agent_name == "random":
        # Seed the pick sequence with the run seed: same game, same walk.
        return (
            RandomAgent(rng_seed=seed),
            None,
            AgentIdentity("random", "builtin", "none", None, None, None),
        )
    if agent_name == "scripted":
        return (
            ScriptedAgent(),
            None,
            AgentIdentity("scripted", "builtin", "none", None, None, None),
        )
    cls, prompt_template = SCAFFOLDS[agent_name]
    provider = config.provider
    if provider is None:
        raise ValueError(f"agent {agent_name!r} needs a model provider; none configured")
    system_prompt = prompt_template.format(character=config.suite.character.upper())
    identity = AgentIdentity(
        model=provider.model,
        base_url=provider.base_url,
        api=config.api or "chat",
        prompt_hash=hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16],
        tool_schema_hash=hashlib.sha256(
            json.dumps(ToolRegistry().openai_tools(), sort_keys=True).encode("utf-8")
        ).hexdigest()[:16],
        reasoning_effort=config.reasoning_effort,
    )
    return cls(provider, max_rounds=config.max_rounds), system_prompt, identity
