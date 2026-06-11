"""Live LLM play: an agent plays the real game through the full stack.

    uv run python -m sts_bench.play --character ironclad --seed STSBENCH1

Start this first (it listens on the relay port), then trigger
CommunicationMod's external process in-game. Backend selection comes from
flags or env (see OpenAICompatProvider.from_env): STS_BENCH_BASE_URL /
STS_BENCH_MODEL / STS_BENCH_API_KEY, or an ANTHROPIC_API_KEY / OPENAI_API_KEY.
A `.env` file in the working directory is loaded automatically (real env vars
win over the file); point --env-file elsewhere if needed.

The scripted smoke policy stays underneath as the safety net: states the
schema cannot parse fall back to raw scripted play (and get captured to
logs/unparsed/), and decision points where the agent burns its budget get a
forced safe action. Both are counted and reported -- a high forced-action
count means the result measures the harness, not the model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .agents import floor, zero_shot
from .actions import Action, translate
from .env import CommunicationModEnv, HarnessServer, StepResult
from .protocol_log import ProtocolLog
from .providers import (
    AnthropicProvider,
    OpenAICompatProvider,
    ProviderError,
    ResponsesProvider,
    Usage,
)
from .smoke import LOG_DIR, MAX_STEPS, choose_action, choose_command, dump_unparsed, fallback_command
from .state import StateMessage, StateParseError, parse_message

MAX_DECISIONS = 1000
LOOP_GUARD_LIMIT = 3
DEFAULT_ENV_FILE = ".env"

AGENTS = {
    "floor": (floor.FloorAgent, floor.SYSTEM_PROMPT),  # one conversation per floor
    "stepwise": (zero_shot.ZeroShotAgent, zero_shot.SYSTEM_PROMPT),  # stateless baseline
}


class LoopGuard:
    """Trips when the same action keeps being chosen from an unchanged game state.

    A stateless agent on a byte-identical state gives a near-identical answer,
    so any screen cycle that doesn't change the world (shop room <-> shop
    screen, map <-> return) loops forever. Counting (state digest, command)
    pairs over the whole run catches such cycles regardless of period; a
    legitimate repeat (playing Strike twice) never trips because the state
    moved in between.
    """

    def __init__(self, limit: int = LOOP_GUARD_LIMIT):
        self._limit = limit
        self._seen: Counter[tuple[str, str]] = Counter()

    def trips(self, game_state: dict, command: str) -> bool:
        digest = hashlib.sha256(
            json.dumps(game_state, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self._seen[digest, command] += 1
        return self._seen[digest, command] >= self._limit


def describe_action(action: Action, message: StateMessage) -> str:
    """Render an executed action in the model's own terms (0-based, named).

    Used for the agent's <recent_decisions> trail, so it must match how the
    model expressed the action -- never the wire command (PLAY is 1-based).
    """
    state = message.game_state
    match action.kind:
        case "play_card":
            combat = state.combat_state if state else None
            card = combat.hand[action.card_index].name if combat else "?"
            target = (
                f" -> {combat.monsters[action.target_index].name} [{action.target_index}]"
                if combat and action.target_index is not None
                else ""
            )
            return f"play_card {action.card_index} ({card}){target}"
        case "choose":
            choices = state.choice_list if state else []
            label = f" ({choices[action.choice_index]})" if action.choice_index < len(choices) else ""
            return f"choose {action.choice_index}{label}"
        case "use_potion" | "discard_potion":
            potions = state.potions if state else []
            label = f" ({potions[action.slot_index].name})" if action.slot_index < len(potions) else ""
            return f"{action.kind} {action.slot_index}{label}"
        case "proceed" | "return_back":
            # These reach the wire as whichever alias the screen offers
            # (proceed/confirm, return/skip/cancel/leave); show the verb.
            try:
                wire = translate(action, message)
            except ValueError:  # narration must not crash on an illegal action
                return action.kind
            return action.kind if wire == action.kind else f"{action.kind} ({wire})"
        case _:
            return action.kind


def _auto_api(base_url_flag: str | None) -> str:
    """Pick the best wire format the backend is known to speak.

    Anthropic backends get the native messages api (thinking, caching, typed
    tool inputs). Everything else gets chat completions, the universal format.
    OpenAI's responses api stays explicit opt-in: its reasoning request is
    rejected by non-reasoning models, and the model's capability cannot be
    detected from its name.
    """
    base_url = base_url_flag or os.environ.get("STS_BENCH_BASE_URL")
    if base_url is not None:
        return "anthropic" if "api.anthropic.com" in base_url else "chat"
    if os.environ.get("STS_BENCH_API_KEY"):
        return "chat"  # explicit key for an unnamed backend: assume compat
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "chat"


def _model_traffic(transcript: list[dict]) -> list[tuple[str, str]]:
    """(tag, text) pairs for the model channel of one decision's new messages.

    The input is the decision's transcript delta, never a full persistent
    conversation -- each message is logged exactly once, by the decision that
    introduced it.

    Arrow direction matches the wire tags (right = sent, left = received);
    the letter names the peer: `>m` is what we sent the model (state view,
    nudges, tool results), `<m` is what it sent back (text, tool calls). The
    system prompt is skipped here -- it is constant, so the runner logs it
    once per session.
    """
    pairs = []
    for msg in transcript:
        role = msg.get("role")
        if role == "user":
            pairs.append((">m", msg.get("content") or ""))
        elif role == "tool":
            pairs.append((">m", f"[{msg.get('tool_call_id')}] {msg.get('content')}"))
        elif role == "assistant":
            for key in ("reasoning_content", "reasoning"):
                if isinstance(msg.get(key), str) and msg[key]:
                    pairs.append(("<m", f"(reasoning) {msg[key]}"))
                    break
            if msg.get("content"):
                pairs.append(("<m", msg["content"]))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                pairs.append(("<m", f"call {fn.get('name')} {fn.get('arguments')}"))
    return pairs


def _reasoning_note(usage: Usage) -> str:
    """' (of which N reasoning)' when the backend reports hidden thinking."""
    return f" (of which {usage.reasoning_tokens} reasoning)" if usage.reasoning_tokens else ""


def _floor_summary(entry: dict, exit_state: dict, decisions: int, usage: Usage) -> str:
    """One line per finished floor: the spend and the damage, reported at the
    boundary -- the first point where the floor's result is observable."""
    return (
        f"floor {entry.get('floor')} summary: {decisions} decisions, "
        f"tokens {usage.prompt_tokens}+{usage.completion_tokens}{_reasoning_note(usage)}, "
        f"HP {entry.get('current_hp')}->{exit_state.get('current_hp')}, "
        f"gold {entry.get('gold')}->{exit_state.get('gold')}"
    )


def _screen_label(game_state: dict) -> str:
    """Screen name for narration. The wire says NONE for combat with no
    overlay on top; call that what it is."""
    screen = game_state.get("screen_type")
    if screen == "NONE":
        return "COMBAT" if game_state.get("combat_state") else "NONE"
    return screen or "?"


def _transition_notes(prev: dict, cur: dict) -> list[str]:
    """Run landmarks worth narrating when the game state moves between steps."""
    notes = []
    if not cur:
        return notes
    if cur.get("floor") != prev.get("floor"):
        notes.append(f"entering floor {cur.get('floor')} (act {cur.get('act')}): {_screen_label(cur)}")
    prev_combat = prev.get("combat_state") or {}
    cur_combat = cur.get("combat_state") or {}
    if cur_combat and not prev_combat:
        monsters = ", ".join(
            m.get("name", "?") for m in cur_combat.get("monsters", []) if not m.get("is_gone")
        )
        notes.append(f"combat starts vs {monsters}")
    elif prev_combat and not cur_combat:
        notes.append("combat over")
    elif cur_combat and cur_combat.get("turn") != prev_combat.get("turn"):
        notes.append(f"combat turn {cur_combat.get('turn')}")
    return notes


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

    env_file = Path(args.env_file)
    if env_file.exists():
        # Report names (never values) of what the file contributes; shell env wins.
        applied = [key for key in dotenv_values(env_file) if key not in os.environ]
        load_dotenv(env_file)
        if applied:
            say(f"loaded {', '.join(applied)} from {env_file}")
    elif args.env_file != DEFAULT_ENV_FILE:
        say(f"env file {env_file} not found")
        return 1

    api = args.api or os.environ.get("STS_BENCH_API") or "auto"
    if api == "auto":
        api = _auto_api(args.base_url)
    providers = {
        "chat": OpenAICompatProvider,  # universal wire format; local/OSS backends
        "responses": ResponsesProvider,  # OpenAI native: visible+carried reasoning
        "anthropic": AnthropicProvider,  # Claude native: thinking, caching, typed tool inputs
    }
    if api not in providers:
        say(f"unknown api {api!r}: expected {', '.join(providers)} or auto")
        return 1
    provider_cls = providers[api]
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
    agent_cls, system_prompt = AGENTS[args.agent]
    agent = agent_cls(provider, max_rounds=args.max_rounds)
    say(f"model: {provider.model} @ {provider.base_url} ({api} api), {args.agent} agent")

    totals = Usage()
    decisions = forced = unparsed = 0
    guard = LoopGuard()

    try:
        with HarnessServer(port=args.port) as server:
            say(f"listening on {server.host}:{server.port} -- start the external process in-game")
            conn = server.accept(timeout=300)
            log = ProtocolLog(LOG_DIR, name="play")
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
            say(f"relay connected from {conn.peer}")

            env = CommunicationModEnv(conn, on_protocol_line=log.line)
            state = env.handshake()

            prev_game_state: dict = {}
            floor_entry: dict = {}
            floor_decisions = 0
            floor_usage = Usage()
            for step_no in range(1, MAX_STEPS + 1):
                game_state = state.get("game_state") or {}
                if game_state and game_state.get("floor") != floor_entry.get("floor"):
                    if floor_entry:
                        say(_floor_summary(floor_entry, prev_game_state, floor_decisions, floor_usage))
                    floor_entry, floor_decisions, floor_usage = game_state, 0, Usage()
                for note in _transition_notes(prev_game_state, game_state):
                    say(note)
                prev_game_state = game_state
                command = None
                history = None  # what to tell the agent actually happened here

                try:
                    message = parse_message(state)
                except StateParseError as exc:
                    unparsed += 1
                    path = dump_unparsed(exc.raw)
                    say(f"state didn't fit the schema; captured -> {path} (scripted fallback)")
                    message = None

                if message is not None and "start" not in message.available_commands:
                    if decisions >= MAX_DECISIONS:
                        say(f"hit MAX_DECISIONS={MAX_DECISIONS}; stopping")
                        break
                    decisions += 1
                    floor_decisions += 1
                    decision = agent.decide(message)
                    totals += decision.usage
                    floor_usage += decision.usage
                    for tag, text in _model_traffic(decision.transcript):
                        for content_line in text.splitlines():
                            log.line(tag, content_line)
                    action = decision.action
                    if action is not None:
                        command = translate(action, message)
                        if guard.trips(game_state, command):
                            say(
                                f"decision {decisions}: loop guard -- "
                                f"{describe_action(action, message)} chosen {LOOP_GUARD_LIMIT}x "
                                f"from an unchanged state; forcing scripted fallback"
                            )
                            decision.forced_reason = "loop guard"
                            action = command = None
                    if action is not None:
                        history = describe_action(action, message)
                        say(
                            f"decision {decisions} ({_screen_label(game_state)}): {history} "
                            f"[rounds {decision.rounds}, lookups {decision.observation_calls}, "
                            f"invalid {decision.invalid_actions}, "
                            f"tokens {decision.usage.prompt_tokens}+{decision.usage.completion_tokens}"
                            f"{_reasoning_note(decision.usage)}]"
                        )
                    else:
                        forced += 1
                        scripted = choose_action(message)
                        command = translate(scripted, message) if scripted else None
                        history = f"{describe_action(scripted, message)} (forced)" if scripted else None
                        say(f"decision {decisions}: FORCED ({decision.forced_reason}) -> {history or command!r}")

                if command is None:
                    command = choose_command(state, args.character, args.ascension, args.seed)
                    if state.get("in_game"):
                        history = f"{command} (scripted)"

                result: StepResult = env.step(command)
                if not result.ok:
                    say(f"step {command!r} failed: {result.error}")
                    retry = fallback_command(result.state)
                    if retry is None:
                        say("no fallback available; stopping")
                        return 1
                    result = env.step(retry)
                    if not result.ok:
                        say(f"fallback {retry!r} also failed: {result.error}; stopping")
                        return 1
                    history = f"{retry} (fallback after {command!r} failed)"
                state = result.state

                if history is not None:
                    agent.record(
                        f"floor {game_state.get('floor')} {_screen_label(game_state)}: {history}"
                    )

                new_game_state = state.get("game_state") or {}
                if new_game_state.get("screen_type") == "GAME_OVER":
                    screen = new_game_state.get("screen_state") or {}
                    say(f"run over: {'VICTORY' if screen.get('victory') else 'DEFEAT'} "
                        f"on floor {new_game_state.get('floor')}, score {screen.get('score')}")
                if not state.get("in_game") and decisions:
                    break
            else:
                say(f"hit MAX_STEPS={MAX_STEPS}; stopping")

    finally:
        # The bottom line prints even when the session dies mid-step
        # (timeout, dropped connection, Ctrl-C): a crashed run still reports.
        say(
            f"totals: {decisions} decisions, {forced} forced, {unparsed} unparsed states, "
            f"tokens {totals.prompt_tokens} prompt + {totals.completion_tokens} completion"
            f"{_reasoning_note(totals)}"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=None, help="model name (default: from env)")
    parser.add_argument("--base-url", default=None, help="chat-completions base URL (default: from env)")
    parser.add_argument(
        "--api",
        choices=("auto", "chat", "responses", "anthropic"),
        default=None,
        help="wire format: auto (default; native messages for Anthropic backends, "
        "chat elsewhere), chat (works everywhere), responses (OpenAI reasoning "
        "models), anthropic (Claude native); env STS_BENCH_API",
    )
    parser.add_argument(
        "--agent",
        choices=tuple(AGENTS),
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
    parser.add_argument("--character", default="ironclad")
    parser.add_argument("--ascension", type=int, default=0)
    parser.add_argument("--max-rounds", type=int, default=10, help="provider calls per decision point")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="env file with keys/config (real env vars win)")
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
