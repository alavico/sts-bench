"""Live smoke check: a dumb scripted agent plays through the full typed stack.

    uv run python -m sts_bench.smoke --seed STSBENCH1 --floors 2

Start this first (it listens on the relay port), then trigger
CommunicationMod's external process in-game with SpireConfig pointing at
relay.py. The agent is deliberately trivial -- play the first playable card,
otherwise end/choose/proceed -- because the point is to exercise the real
pipeline (parse -> typed action -> validate -> translate -> step), not to win.

States the schema cannot parse yet don't stop the run: the payload is dumped
to logs/unparsed/ (harvest it into a fixture, extend the schema) and a raw
dict fallback keeps playing.

`--floors N` stops once floor N is reached (reaching floor 2 means the floor-1
fight was completed); the default plays until the run ends.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .actions import translate, validate
from .agents.heuristic import scripted_action
from .env import CommunicationModEnv, HarnessServer, RawState, StepResult
from .protocol_log import ProtocolLog
from .state import StateParseError, parse_message

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
UNPARSED_DIR = LOG_DIR / "unparsed"
MAX_STEPS = 3000


def dump_unparsed(raw: RawState) -> Path:
    UNPARSED_DIR.mkdir(parents=True, exist_ok=True)
    existing = len(list(UNPARSED_DIR.glob("*.json")))
    path = UNPARSED_DIR / f"unparsed-{existing + 1}.json"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def choose_command(state: RawState, character: str, ascension: int, seed: str | None) -> str:
    commands = state.get("available_commands", [])
    game_state = state.get("game_state") or {}

    if "play" in commands:
        combat = game_state.get("combat_state") or {}
        target = _first_alive_monster(combat.get("monsters", []))
        for i, card in enumerate(combat.get("hand", [])):
            if not card.get("is_playable"):
                continue
            if card.get("has_target"):
                if target is None:
                    continue
                return f"play {i + 1} {target}"  # PLAY hand index is 1-based
            return f"play {i + 1}"
    if "end" in commands:
        return "end"
    if "choose" in commands:
        return "choose 0"
    if "proceed" in commands:
        return "proceed"
    if "confirm" in commands:
        return "confirm"
    if "start" in commands:
        seed_part = f" {seed}" if seed else ""
        return f"start {character} {ascension}{seed_part}"
    return "state"


def fallback_command(state: RawState) -> str | None:
    """When the preferred command is rejected, take any way forward."""
    commands = state.get("available_commands", [])
    for cmd in ("end", "proceed", "confirm", "skip", "return", "cancel", "leave"):
        if cmd in commands:
            return cmd
    if "choose" in commands:
        return "choose 0"
    return None


def _first_alive_monster(monsters: list[dict]) -> int | None:
    for i, monster in enumerate(monsters):
        if not monster.get("is_gone") and not monster.get("half_dead") and monster.get("current_hp", 0) > 0:
            return i
    return None


def _progress(state: RawState) -> str:
    game_state = state.get("game_state") or {}
    if not state.get("in_game"):
        return "out of game"
    return (
        f"floor {game_state.get('floor')} act {game_state.get('act')} "
        f"HP {game_state.get('current_hp')}/{game_state.get('max_hp')} "
        f"screen={game_state.get('screen_type')}"
    )


def run(args: argparse.Namespace) -> int:
    say = lambda msg: print(f"[smoke] {msg}", file=sys.stderr)

    with HarnessServer(port=args.port) as server:
        say(f"listening on {server.host}:{server.port} -- start the external process in-game")
        conn = server.accept(timeout=300)
        # Only now is there a run worth a logfile; aborted startups leave none.
        log = ProtocolLog(LOG_DIR, name="smoke")
        say(f"protocol log: {log.path}")
        say(f"relay connected from {conn.peer}")

        env = CommunicationModEnv(conn, on_protocol_line=log.line)
        state = env.handshake()
        say(f"handshake ok: {_progress(state)}")

        last_floor = None
        for step_no in range(1, MAX_STEPS + 1):
            game_state = state.get("game_state") or {}
            floor = game_state.get("floor")
            if floor != last_floor and floor is not None:
                say(f"step {step_no}: {_progress(state)}")
                last_floor = floor
            if args.floors and floor is not None and floor >= args.floors:
                say(f"SUCCESS: reached floor {floor} in {step_no} steps")
                return 0

            command = None
            try:
                message = parse_message(state)
            except StateParseError as exc:
                path = dump_unparsed(exc.raw)
                say(f"state didn't fit the schema; captured -> {path} (raw fallback)")
            else:
                action = scripted_action(message)
                if action is not None:
                    verdict = validate(action, message)
                    if verdict.ok:
                        command = translate(action, message)
                    else:
                        say(f"validator rejected scripted {action.kind}: {verdict.reason} (raw fallback)")
            if command is None:
                command = choose_command(state, args.character, args.ascension, args.seed)
            result: StepResult = env.step(command)
            if not result.ok:
                say(f"rejected: {command!r} -> {result.error}")
                retry = fallback_command(result.state)
                if retry is None:
                    say("no fallback available; stopping")
                    return 1
                result = env.step(retry)
                if not result.ok:
                    say(f"fallback {retry!r} also rejected: {result.error}; stopping")
                    return 1
            state = result.state

            if not state.get("in_game") and command.startswith(("proceed", "confirm")):
                say("run ended (back out of game)")
                return 0

        say(f"hit MAX_STEPS={MAX_STEPS}; stopping")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", default="STSBENCH1", help="run seed (alphanumeric); empty string for random")
    parser.add_argument("--character", default="ironclad")
    parser.add_argument("--ascension", type=int, default=0)
    parser.add_argument("--floors", type=int, default=0, help="stop once this floor is reached (0 = play to the end)")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
