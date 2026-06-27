"""Live LLM play: an agent plays the real game through the full stack.

    uv run python -m sts_bench.play --character ironclad --seed STSBENCH1

Start this first (it listens on the relay port), then trigger
CommunicationMod's external process in-game. Backend selection comes from
flags or env (see OpenAICompatProvider.from_env): STS_BENCH_BASE_URL /
STS_BENCH_MODEL / STS_BENCH_API_KEY, or an ANTHROPIC_API_KEY / OPENAI_API_KEY.
A `.env` file in the working directory is loaded automatically (real env vars
win over the file); point --env-file elsewhere if needed.

The run itself is the shared loop in runner.session: the scripted smoke
policy underneath as the safety net, every fallback counted and reported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from .agents import SCAFFOLDS
from .cli import add_character_ascension_args, add_env_file_arg, apply_env_file
from .env import CommunicationModEnv, HarnessServer
from .protocol_log import ProtocolLog, reasoning_note
from .providers import PROVIDERS, ProviderError, auto_api
from .runner.session import RunTally, play_run
from .smoke import session_dir
from .tools import ToolRegistry
from .trajectory import RunRecorder, TrajectoryStore


def run(args: argparse.Namespace) -> int:
    # No logfile until a game actually connects: aborted startups (bad env,
    # missing key, never launching the game) must not litter logs/.
    log: ProtocolLog | None = None

    def say(msg: str) -> None:
        """Narrate to the terminal, and into the log (as a `--` line) once it
        exists, so the logfile reads as wire traffic interleaved with the story."""
        print(f"[play] {msg}", file=sys.stderr)
        if log is not None:
            log.line("--", msg)

    if not apply_env_file(args.env_file, say):
        return 1

    api = args.api or os.environ.get("STS_BENCH_API") or "auto"
    if api == "auto":
        api = auto_api(args.base_url)
    if api not in PROVIDERS:
        say(f"unknown api {api!r}: expected {', '.join(PROVIDERS)} or auto")
        return 1
    provider_cls = PROVIDERS[api]
    provider_kwargs = (
        {"reasoning_effort": args.reasoning_effort} if args.reasoning_effort else {}
    )
    try:
        provider = provider_cls.from_env(
            model=args.model, base_url=args.base_url, **provider_kwargs
        )
    except ProviderError as exc:
        say(str(exc))
        return 1
    agent_cls, system_prompt = SCAFFOLDS[args.agent]
    agent = agent_cls(provider, max_rounds=args.max_rounds)
    say(f"model: {provider.model} @ {provider.base_url} ({api} api), {args.agent} agent")

    tally = RunTally()
    store: TrajectoryStore | None = None
    recorder: RunRecorder | None = None

    try:
        with HarnessServer(port=args.port) as server:
            say(f"listening on {server.host}:{server.port} -- start the external process in-game")
            conn = server.accept(timeout=300)
            log = ProtocolLog(session_dir(), name="play")
            effort_note = f", effort {args.reasoning_effort}" if args.reasoning_effort else ""
            log.line(
                "--",
                f"model: {provider.model} @ {provider.base_url} ({api} api{effort_note}) | "
                f"{args.agent} agent | {args.character} ascension {args.ascension} seed {args.seed or 'random'}",
            )
            log.line("--", "system prompt (constant; logged once):")
            for prompt_line in system_prompt.format(character=args.character.upper()).splitlines():
                log.line(">m", prompt_line)
            say(f"protocol log: {log.path}")
            # The trajectory file shares the protocol log's name and session
            # folder, so the wire view and the record view of one run pair up
            # by filename within the day's logs.
            store = TrajectoryStore(log.path.parent / "trajectories", run_id=log.path.stem)
            recorder = RunRecorder(
                store,
                run_id=log.path.stem,
                seed=args.seed or None,
                character=args.character,
                ascension=args.ascension,
                model=provider.model,
                provider_base_url=provider.base_url,
                api=api,
                reasoning_effort=args.reasoning_effort,
                agent=args.agent,
                prompt_hash=hashlib.sha256(
                    system_prompt.format(character=args.character.upper()).encode("utf-8")
                ).hexdigest()[:16],
                tool_schema_hash=hashlib.sha256(
                    json.dumps(ToolRegistry().openai_tools(), sort_keys=True).encode("utf-8")
                ).hexdigest()[:16],
            )
            say(f"trajectory: {store.path}")
            say(f"relay connected from {conn.peer}")

            env = CommunicationModEnv(conn, on_protocol_line=log.line)
            state = env.handshake()
            play_run(
                env,
                state,
                agent,
                recorder,
                character=args.character,
                ascension=args.ascension,
                seed=args.seed or None,
                log=log,
                say=say,
                tally=tally,
            )

    finally:
        # The bottom line prints even when the session dies mid-step
        # (timeout, dropped connection, Ctrl-C): a crashed run still reports,
        # and the trajectory closes with whatever floors completed.
        if recorder is not None:
            recorder.finish()
        if store is not None:
            store.close()
        say(
            f"totals: {tally.decisions} decisions, {tally.forced} forced, "
            f"{tally.unparsed} unparsed states, "
            f"tokens {tally.usage.prompt_tokens} prompt + {tally.usage.completion_tokens} completion"
            f"{reasoning_note(tally.usage)}"
        )
    return 1 if tally.fatal else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=None, help="model name (default: from env)")
    parser.add_argument("--base-url", default=None, help="chat-completions base URL (default: from env)")
    parser.add_argument(
        "--api",
        choices=("auto", *PROVIDERS),
        default=None,
        help="wire format: auto (default; native messages for Anthropic/Gemini "
        "backends, chat elsewhere), chat (works everywhere), responses (OpenAI "
        "reasoning models), anthropic (Claude native), gemini (Gemini native: "
        "thought summaries); env STS_BENCH_API",
    )
    parser.add_argument(
        "--agent",
        choices=tuple(SCAFFOLDS),
        default="floor",
        help="context scaffold: floor keeps one conversation per floor (default); "
        "stepwise starts fresh at every decision (the stateless ablation baseline)",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="ask the model to deliberate, surfacing what reasoning it can: "
        "OpenAI takes minimal/low/medium/high; Claude takes low/medium/high/max "
        "(and this also turns adaptive thinking on). Unset = provider default, "
        "which for gpt-5-class models means almost no reasoning.",
    )
    parser.add_argument("--seed", default="STSBENCH1", help="run seed (alphanumeric); empty string for random")
    add_character_ascension_args(parser)
    parser.add_argument("--max-rounds", type=int, default=10, help="provider calls per decision point")
    parser.add_argument("--port", type=int, default=9999)
    add_env_file_arg(parser)
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
