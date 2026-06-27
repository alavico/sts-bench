"""Resume a stopped LLM run from its trajectory checkpoint.

    uv run python -m sts_bench.resume logs/<date>/trajectories/play-<timestamp>.jsonl

The trajectory is the resume source of truth: it carries the model/API config,
the seed and character, every command needed to replay the live game back to
the checkpoint, and the current floor conversation needed by the agent.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import SCAFFOLDS
from .agents.floor import FloorAgent
from .agents.zero_shot import ZeroShotAgent
from .cli import add_env_file_arg, apply_env_file
from .drive import DEFAULT_DELAY, command_divergence, entry_diffs, plan_replay
from .env import CommunicationModEnv, HarnessServer, StepResult
from .protocol_log import ProtocolLog, reasoning_note
from .providers import PROVIDERS, ProviderError, Usage
from .runner.session import RunTally, play_run
from .smoke import choose_command, session_dir
from .tools import ToolRegistry
from .trajectory import (
    DecisionRecord,
    FloorRecord,
    RunRecord,
    RunRecorder,
    TrajectoryRecord,
    TrajectoryStore,
    read_records,
)

MENU_STEP_CAP = 50


@dataclass(frozen=True)
class Checkpoint:
    run: RunRecord
    decisions: list[DecisionRecord]
    current_floor: FloorRecord | None
    records_to_keep: list[TrajectoryRecord]
    pruned_records: list[TrajectoryRecord]

    @property
    def latest_decision(self) -> DecisionRecord:
        return self.decisions[-1]


class ResumeError(Exception):
    pass


def load_checkpoint(path: Path) -> Checkpoint:
    records = list(read_records(path))
    run_index = next(
        (i for i in range(len(records) - 1, -1, -1) if isinstance(records[i], RunRecord)),
        None,
    )
    if run_index is None:
        raise ResumeError("trajectory has no run record; stop the original run cleanly first")
    run = records[run_index]
    assert isinstance(run, RunRecord)
    if run.outcome.victory is not None:
        raise ResumeError("trajectory already reached game over; there is nothing to resume")
    decisions = sorted(
        (r for r in records if isinstance(r, DecisionRecord)),
        key=lambda d: d.decision_index,
    )
    if not decisions:
        raise ResumeError("trajectory has no decisions to resume from")

    keep = list(records)
    pruned: list[TrajectoryRecord] = []
    pruned.append(keep.pop(run_index))

    floor_index = next(
        (
            i
            for i in range(len(keep) - 1, -1, -1)
            if isinstance(keep[i], FloorRecord)
        ),
        None,
    )
    current_floor = None
    if floor_index is not None:
        current_floor = keep[floor_index]
        assert isinstance(current_floor, FloorRecord)
        pruned.append(keep.pop(floor_index))

    return Checkpoint(
        run=run,
        decisions=decisions,
        current_floor=current_floor,
        records_to_keep=keep,
        pruned_records=pruned,
    )


def rewrite_checkpoint(path: Path, checkpoint: Checkpoint, *, backup: bool = True) -> Path | None:
    backup_path = None
    if backup:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup_path)
    with path.open("w", encoding="utf-8") as fh:
        for record in checkpoint.records_to_keep:
            fh.write(record.model_dump_json() + "\n")
    return backup_path


def _history_line(decision: DecisionRecord) -> str:
    if decision.action:
        history = decision.action
    elif decision.command:
        forced = "forced" if decision.forced_reason else "scripted"
        history = f"{decision.command} ({forced})"
    else:
        history = decision.forced_reason or "no command recorded"
    return f"floor {decision.floor} {decision.screen}: {history}"


def _tool_calls(message: dict[str, Any]) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        if call.get("id") and fn.get("name"):
            calls.append((call["id"], fn["name"]))
    for block in message.get("_blocks") or []:
        if block.get("type") == "tool_use" and block.get("id") and block.get("name"):
            calls.append((block["id"], block["name"]))
    return calls


def pending_action_call_id(messages: list[dict[str, Any]]) -> str | None:
    registry = ToolRegistry()
    answered = {
        m.get("tool_call_id")
        for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        for call_id, name in reversed(_tool_calls(message)):
            if call_id not in answered and registry.kind_of(name) == "action":
                return call_id
    return None


def hydrate_agent(agent: Any, checkpoint: Checkpoint, checkpoint_state: dict[str, Any]) -> None:
    current_decisions = [
        d for d in checkpoint.decisions if d.floor == checkpoint.latest_decision.floor
    ]
    history = [_history_line(d) for d in current_decisions]
    if isinstance(agent, FloorAgent):
        conversation = (
            checkpoint.current_floor.conversation
            if checkpoint.current_floor and checkpoint.current_floor.floor == checkpoint.latest_decision.floor
            else []
        )
        pending = pending_action_call_id(conversation)
        agent.resume(
            floor=checkpoint.latest_decision.floor,
            conversation=conversation,
            pending_call_id=pending,
            outcome=history[-1] if history else None,
            floor_outcomes=history,
            in_combat=bool((checkpoint_state or {}).get("combat_state")),
        )
    elif isinstance(agent, ZeroShotAgent):
        agent.resume(_history_line(d) for d in checkpoint.decisions[-8:])


def replay_to_checkpoint(
    env: CommunicationModEnv,
    checkpoint: Checkpoint,
    *,
    delay: float,
    say,
) -> dict[str, Any]:
    plan = plan_replay(
        [*checkpoint.records_to_keep, *checkpoint.pruned_records],
        until_decision=checkpoint.latest_decision.decision_index,
        inclusive=True,
    )
    state = env.handshake()
    menu_steps = 0
    while not state.get("in_game"):
        if menu_steps >= MENU_STEP_CAP:
            raise ResumeError(f"still at the menu after {MENU_STEP_CAP} steps")
        command = choose_command(state, plan.character, plan.ascension, plan.seed)
        result = env.step(command)
        if not result.ok:
            raise ResumeError(f"menu step {command!r} rejected: {result.error}")
        state = result.state
        menu_steps += 1

    say(f"replaying {len(plan.commands)} recorded commands to decision {plan.target_index}")
    verified_floors: set[int] = set()
    seen_floor = (state.get("game_state") or {}).get("floor")
    for n, step in enumerate(plan.steps, start=1):
        command = step.command
        assert command is not None
        drift = command_divergence(step, state.get("game_state") or {})
        if drift:
            raise ResumeError(f"decision {step.decision_index}: replay diverged: {drift}")
        result: StepResult = env.step(command)
        if not result.ok:
            raise ResumeError(f"command {n}/{len(plan.steps)} {command!r} rejected: {result.error}")
        state = result.state
        game_state = state.get("game_state") or {}
        floor = game_state.get("floor")
        if floor != seen_floor and floor in plan.floor_entries and floor not in verified_floors:
            diffs = entry_diffs(game_state, plan.floor_entries[floor])
            if diffs:
                raise ResumeError(f"floor {floor} boundary mismatch: {'; '.join(diffs)}")
            say(
                f"floor {floor} boundary verified "
                f"(hp {game_state.get('current_hp')}, gold {game_state.get('gold')})"
            )
            verified_floors.add(floor)
        seen_floor = floor
        if delay:
            time.sleep(delay)
    return state


def _provider(run: RunRecord, say):
    provider_cls = PROVIDERS.get(run.api)
    if provider_cls is None:
        raise ResumeError(f"unknown recorded api {run.api!r}: expected {', '.join(PROVIDERS)}")
    kwargs = {"reasoning_effort": run.reasoning_effort} if run.reasoning_effort else {}
    try:
        return provider_cls.from_env(model=run.model, base_url=run.provider_base_url, **kwargs)
    except ProviderError as exc:
        raise ResumeError(str(exc)) from exc


def run(args: argparse.Namespace) -> int:
    def say(msg: str) -> None:
        print(f"[resume] {msg}", file=sys.stderr)
        if log is not None:
            log.line("--", msg)

    path = Path(args.path)
    if not path.exists():
        print(f"[resume] no such trajectory: {path}", file=sys.stderr)
        return 1
    log: ProtocolLog | None = None
    if not apply_env_file(args.env_file, lambda msg: print(f"[resume] {msg}", file=sys.stderr)):
        return 1
    try:
        checkpoint = load_checkpoint(path)
        provider = _provider(checkpoint.run, say)
        agent_cls, _system_prompt = SCAFFOLDS[checkpoint.run.agent]
        agent = agent_cls(provider, max_rounds=args.max_rounds)
    except ResumeError as exc:
        print(f"[resume] {exc}", file=sys.stderr)
        return 1

    store: TrajectoryStore | None = None
    recorder: RunRecorder | None = None
    tally = RunTally(
        decisions=max(d.decision_index for d in checkpoint.decisions),
        forced=sum(1 for d in checkpoint.decisions if d.forced_reason is not None),
        usage=Usage(
            prompt_tokens=sum(d.usage.prompt_tokens for d in checkpoint.decisions),
            completion_tokens=sum(d.usage.completion_tokens for d in checkpoint.decisions),
            reasoning_tokens=sum(d.usage.reasoning_tokens for d in checkpoint.decisions),
            cache_read_tokens=sum(d.usage.cache_read_tokens for d in checkpoint.decisions),
        ),
    )
    try:
        with HarnessServer(port=args.port) as server:
            print(
                f"[resume] listening on {server.host}:{server.port} -- start the external process in-game",
                file=sys.stderr,
            )
            conn = server.accept(timeout=300)
            log = ProtocolLog(session_dir(), name=f"{checkpoint.run.run_id}-resume")
            say(f"resume trace: {log.path}")
            env = CommunicationModEnv(conn, on_protocol_line=log.line)
            state = replay_to_checkpoint(env, checkpoint, delay=args.delay, say=say)
            game_state = state.get("game_state") or {}
            say(
                f"checkpoint reached: floor {game_state.get('floor')} "
                f"HP {game_state.get('current_hp')}/{game_state.get('max_hp')} "
                f"screen {game_state.get('screen_type')}"
            )
            backup = rewrite_checkpoint(path, checkpoint, backup=not args.no_backup)
            if backup is not None:
                say(f"backup before resume edits: {backup}")
            store = TrajectoryStore(path.parent, path.stem)
            recorder = RunRecorder.resume(
                store,
                run=checkpoint.run,
                decisions=checkpoint.decisions,
                current_floor=checkpoint.current_floor,
                checkpoint_state=game_state,
            )
            hydrate_agent(agent, checkpoint, game_state)
            say(
                f"continuing {checkpoint.run.run_id}: {provider.model} "
                f"({checkpoint.run.api} api), {checkpoint.run.agent} agent"
            )
            play_run(
                env,
                state,
                agent,
                recorder,
                character=checkpoint.run.character,
                ascension=checkpoint.run.ascension,
                seed=checkpoint.run.seed,
                log=log,
                say=say,
                tally=tally,
            )
    except ResumeError as exc:
        say(str(exc)) if log is not None else print(f"[resume] {exc}", file=sys.stderr)
        return 1
    finally:
        if recorder is not None:
            recorder.finish()
        if store is not None:
            store.close()
        if log is not None:
            say(
                f"totals: {tally.decisions} decisions, {tally.forced} forced, "
                f"{tally.unparsed} unparsed states, "
                f"tokens {tally.usage.prompt_tokens} prompt + {tally.usage.completion_tokens} completion"
                f"{reasoning_note(tally.usage)}"
            )
            log.close()
    return 1 if tally.fatal else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="trajectory JSONL file to resume")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        metavar="SECONDS",
        help=f"pause between replayed commands so the game settles (default {DEFAULT_DELAY}; 0 to disable)",
    )
    parser.add_argument("--max-rounds", type=int, default=10, help="provider calls per decision point")
    parser.add_argument("--no-backup", action="store_true", help="rewrite the trajectory without a .bak copy")
    add_env_file_arg(parser)
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
