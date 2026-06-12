"""Shape one run's trajectory records into the report's data: plain dicts,
ready to embed as JSON.

The structured records carry the economics directly -- scorecards, rewards,
token spend, decision costs. What they hold only as conversation text -- the
turn-by-turn course of each fight, the route walked across the act maps -- is
mined from the stored digests (see digest). Everything is shaped here, in
Python, so the page script stays a renderer.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..replay import group, verify_floor
from ..trajectory import DecisionRecord, FloorRecord, RunRecord
from ..trajectory.schema import TokenUsage
from .digest import MapChoice, combat_snapshot, map_choice


def build_report_data(records: list) -> dict[str, Any]:
    run, floors, decisions = group(records)
    known = {floor.floor for floor in floors}
    orphans = [
        {"floor": floor_no, "decisions": len(decs)}
        for floor_no, decs in sorted(
            decisions.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)
        )
        if floor_no not in known
    ]
    return {
        "run": _run(run, floors),
        "system_prompt": _system_prompt(floors),
        "floors": [_floor(floor, decisions.get(floor.floor, [])) for floor in floors],
        "acts": _acts(floors, decisions),
        "orphans": orphans,
    }


def _run(run: RunRecord | None, floors: list[FloorRecord]) -> dict[str, Any]:
    if run is None:
        # The run died mid-flight: no run record was ever written. The floors
        # that completed still tell their story.
        return {
            "missing": True,
            "run_id": floors[0].run_id if floors else "unknown",
            "verdict": "UNFINISHED",
            "floor_reached": floors[-1].floor if floors else None,
            "score": None,
            "model": None,
            "agent": None,
            "character": None,
            "ascension": None,
            "seed": None,
            "api": None,
            "reasoning_effort": None,
            "started_at": None,
            "finished_at": None,
            "decisions": None,
            "forced": None,
            "unparsed": None,
            "usage": None,
        }
    verdict = {True: "VICTORY", False: "DEFEAT"}.get(run.outcome.victory, "UNFINISHED")
    return {
        "missing": False,
        "run_id": run.run_id,
        "verdict": verdict,
        "floor_reached": run.outcome.floor_reached,
        "score": run.outcome.score,
        "model": run.model,
        "agent": run.agent,
        "character": run.character,
        "ascension": run.ascension,
        "seed": run.seed,
        "api": run.api,
        "reasoning_effort": run.reasoning_effort,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "decisions": run.totals.decisions,
        "forced": run.totals.forced,
        "unparsed": run.totals.unparsed_states,
        "usage": _usage(run.totals.usage),
    }


def _system_prompt(floors: list[FloorRecord]) -> str | None:
    for floor in floors:
        if floor.conversation and floor.conversation[0].get("role") == "system":
            return str(floor.conversation[0].get("content") or "")
    return None


def _usage(usage: TokenUsage) -> dict[str, int]:
    return {
        "prompt": usage.prompt_tokens,
        "completion": usage.completion_tokens,
        "reasoning": usage.reasoning_tokens,
        "cached": usage.cache_read_tokens,
    }


def _floor(floor: FloorRecord, decisions: list[DecisionRecord]) -> dict[str, Any]:
    scorecard = floor.scorecard
    totals = TokenUsage()
    latency = 0
    for decision in decisions:
        totals.prompt_tokens += decision.usage.prompt_tokens
        totals.completion_tokens += decision.usage.completion_tokens
        totals.reasoning_tokens += decision.usage.reasoning_tokens
        totals.cache_read_tokens += decision.usage.cache_read_tokens
        latency += decision.latency_ms or 0
    return {
        "floor": floor.floor,
        "type": floor.floor_type,
        "act": floor.entry_state.get("act"),
        "entry": {"hp": floor.entry.hp, "max_hp": floor.entry.max_hp, "gold": floor.entry.gold},
        "exit": {"hp": floor.exit.hp, "max_hp": floor.exit.max_hp, "gold": floor.exit.gold},
        "scorecard": {
            "hp_delta": scorecard.hp_delta,
            "gold_delta": scorecard.gold_delta,
            "cards_gained": scorecard.cards_gained,
            "relics_gained": scorecard.relics_gained,
            "potions_gained": scorecard.potions_gained,
            "combat_turns": scorecard.combat_turns,
        },
        "reward": (
            {
                "spec": floor.reward.spec_version,
                "total": floor.reward.total,
                "components": floor.reward.components,
            }
            if floor.reward is not None
            else None
        ),
        "violations": verify_floor(floor, decisions),
        "usage": _usage(totals),
        "latency_ms": latency,
        "turns": _turns(floor, decisions),
        "decisions": [_decision(floor, decision) for decision in decisions],
    }


def _decision(floor: FloorRecord, decision: DecisionRecord) -> dict[str, Any]:
    return {
        "index": decision.decision_index,
        "screen": decision.screen,
        "action": decision.action,
        "command": decision.command,
        "forced": decision.forced_reason,
        "rounds": decision.rounds,
        "lookups": decision.observation_calls,
        "invalid": decision.invalid_actions,
        "latency_ms": decision.latency_ms,
        "usage": _usage(decision.usage),
        "events": _events(floor.conversation[decision.message_start : decision.message_end]),
    }


def _events(messages: list[dict]) -> list[dict[str, Any]]:
    """One decision's slice of the conversation as renderable events, the
    same wire shapes the protocol log decodes. The system message is skipped:
    it is constant, shown once at the top of the page."""
    events: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            events.append({"role": "user", "kind": "state", "text": str(message.get("content") or "")})
        elif role == "tool":
            content = str(message.get("content") or "")
            kind = "rejection" if content.startswith("action rejected") else "result"
            events.append({"role": "tool", "kind": kind, "text": content})
        elif role == "assistant":
            for key in ("reasoning_content", "reasoning"):
                value = message.get(key)
                if isinstance(value, str) and value:
                    events.append({"role": "assistant", "kind": "reasoning", "text": value})
                    break
            content = message.get("content")
            if content:
                text = content if isinstance(content, str) else json.dumps(content)
                events.append({"role": "assistant", "kind": "text", "text": text})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                name = function.get("name") or "?"
                arguments = function.get("arguments") or ""
                events.append(
                    {
                        "role": "assistant",
                        "kind": "call",
                        "name": name,
                        "text": f"{name} {arguments}".strip(),
                    }
                )
    return events


def _turns(floor: FloorRecord, decisions: list[DecisionRecord]) -> list[dict[str, Any]] | None:
    """The floor's fights, turn by turn: each decision's digest names the turn
    it acted on, so grouping consecutive digests by turn number rebuilds the
    fight. A turn number falling back to 1 marks a second fight on the floor.
    """
    turns: list[dict[str, Any]] = []
    for decision in decisions:
        snapshot = None
        for message in floor.conversation[decision.message_start : decision.message_end]:
            if message.get("role") == "user":
                snapshot = combat_snapshot(str(message.get("content") or ""))
                if snapshot is not None:
                    break
        if snapshot is None:
            continue
        if not turns or turns[-1]["turn"] != snapshot.turn:
            turns.append(
                {
                    "turn": snapshot.turn,
                    "hp": snapshot.hp,
                    "max_hp": snapshot.max_hp,
                    "block": snapshot.block,
                    "energy": snapshot.energy,
                    "enemies": [asdict(enemy) for enemy in snapshot.enemies],
                    "actions": [],
                }
            )
        label = decision.action or (
            f"forced: {decision.forced_reason}" if decision.forced_reason else decision.command or "?"
        )
        turns[-1]["actions"].append(
            {
                "decision": decision.decision_index,
                "text": label,
                "forced": bool(decision.forced_reason),
                "invalid": decision.invalid_actions,
            }
        )
    return turns or None


def _acts(
    floors: list[FloorRecord], decisions: dict[int | None, list[DecisionRecord]]
) -> list[dict[str, Any]]:
    """Each act's map and the route walked on it.

    A `choose` on the MAP screen of floor f enters the node that becomes
    floor f+1 -- which is why the choice on a boss-chest floor belongs to the
    *next* act's map. The digest names the chosen column (x); the row is the
    count of steps already walked in that act.
    """
    act_of_floor = {floor.floor: floor.entry_state.get("act") for floor in floors}
    floor_lookup = {floor.floor: floor for floor in floors}

    acts: dict[int, dict[str, Any]] = {}
    for floor in floors:
        act = floor.entry_state.get("act")
        if act is None:
            continue
        entry = acts.setdefault(
            act, {"act": act, "boss": None, "nodes": [], "edges": [], "path": []}
        )
        if entry["boss"] is None:
            entry["boss"] = floor.entry_state.get("act_boss")
        if not entry["nodes"] and floor.entry_state.get("map"):
            for node in floor.entry_state["map"]:
                entry["nodes"].append(
                    {"x": node["x"], "y": node["y"], "symbol": node.get("symbol")}
                )
                for child in node.get("children", []):
                    entry["edges"].append([node["x"], node["y"], child["x"], child["y"]])

    for floor in floors:
        for decision in decisions.get(floor.floor, []):
            if decision.screen != "MAP" or not (decision.command or "").startswith("choose"):
                continue
            choice = _map_digest(floor_lookup[floor.floor], decision)
            if choice is None:
                continue
            next_floor = floor.floor + 1
            act = act_of_floor.get(next_floor, act_of_floor.get(floor.floor))
            entry = acts.get(act)
            if entry is None:
                continue
            if choice.boss and not choice.options:
                entry["path"].append({"boss": True, "floor": next_floor})
                continue
            try:
                index = int((decision.command or "").split()[1])
            except (IndexError, ValueError):
                continue
            x = choice.options.get(index)
            if x is None:
                continue
            row = sum(1 for step in entry["path"] if not step.get("boss"))
            node = next(
                (n for n in entry["nodes"] if n["y"] == row and n["x"] == x), None
            )
            if node is None:
                continue
            entry["path"].append({**node, "floor": next_floor})

    return [acts[act] for act in sorted(acts)]


def _map_digest(floor: FloorRecord, decision: DecisionRecord) -> MapChoice | None:
    for message in floor.conversation[decision.message_start : decision.message_end]:
        if message.get("role") == "user":
            parsed = map_choice(str(message.get("content") or ""))
            if parsed is not None:
                return parsed
    return None
