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
import re
from dataclasses import asdict
from typing import Any

from ..replay import group, verify_floor
from ..state.schema import Card, Potion, Relic
from ..tools.card_db import card_text
from ..tools.potion_db import potion_text
from ..tools.relic_db import relic_text
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
        "deck": _deck(floors[-1].exit_state) if floors else None,
        "relics": _collected(floors, "relics", "relics_gained", _relic_text),
        "potions": _potions(floors, decisions),
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
        "deck_changes": _deck_changes(floor.entry_state, floor.exit_state),
        "violations": verify_floor(floor, decisions),
        "usage": _usage(totals),
        "latency_ms": latency,
        "turns": _turns(floor, decisions),
        "decisions": [_decision(floor, decision) for decision in decisions],
    }


# -- deck, relics, potions -----------------------------------------------------

_TYPE_ORDER = {"ATTACK": 0, "SKILL": 1, "POWER": 2, "STATUS": 3, "CURSE": 4}


def _card_name(card: dict) -> str:
    """Display name with the upgrade marker the game uses ("Bash+"); the mod
    usually bakes it into `name` already, so only add it when missing."""
    name = str(card.get("name") or "?")
    upgrades = card.get("upgrades") or 0
    if upgrades and not name.endswith("+") and "+" not in name:
        name += "+" if upgrades == 1 else f"+{upgrades}"
    return name


def _card_text(card: dict) -> str | None:
    try:
        return card_text(Card.model_validate({
            "id": card.get("id") or card.get("name") or "?",
            "uuid": card.get("uuid") or "",
            "name": card.get("name") or "?",
            "type": card.get("type") or "SKILL",
            "rarity": card.get("rarity") or "SPECIAL",
            "cost": card.get("cost") or 0,
            "upgrades": card.get("upgrades") or 0,
            "has_target": bool(card.get("has_target")),
            "exhausts": bool(card.get("exhausts")),
            "ethereal": bool(card.get("ethereal")),
        }))
    except ValueError:
        return None


def _deck(state: dict) -> list[dict[str, Any]] | None:
    """The deck grouped for display: one entry per distinct card (upgrades
    split copies apart), ordered the way a player scans a deck list --
    attacks, skills, powers, then dross."""
    cards = state.get("deck")
    if not isinstance(cards, list) or not cards:
        return None
    groups: dict[str, dict[str, Any]] = {}
    for card in cards:
        name = _card_name(card)
        entry = groups.get(name)
        if entry is None:
            groups[name] = entry = {
                "name": name,
                "count": 0,
                "cost": card.get("cost"),
                "type": card.get("type"),
                "rarity": card.get("rarity"),
                "text": _card_text(card),
            }
        entry["count"] += 1
    return sorted(
        groups.values(),
        key=lambda c: (
            _TYPE_ORDER.get(c["type"], len(_TYPE_ORDER)),
            c["cost"] if isinstance(c["cost"], int) and c["cost"] >= 0 else 99,
            c["name"],
        ),
    )


def _deck_changes(entry_state: dict, exit_state: dict) -> dict[str, list[str]] | None:
    """What happened to the deck on this floor, told by card identity: uuids
    that appear are gains, uuids that vanish are removals, and a uuid whose
    upgrade count climbed was smithed."""
    before = {c["uuid"]: c for c in entry_state.get("deck") or [] if c.get("uuid")}
    after = {c["uuid"]: c for c in exit_state.get("deck") or [] if c.get("uuid")}
    if not before or not after:
        return None
    added = [_card_name(c) for u, c in after.items() if u not in before]
    removed = [_card_name(c) for u, c in before.items() if u not in after]
    upgraded = [
        _card_name(c)
        for u, c in after.items()
        if u in before and (c.get("upgrades") or 0) > (before[u].get("upgrades") or 0)
    ]
    if not (added or removed or upgraded):
        return None
    return {"added": sorted(added), "removed": sorted(removed), "upgraded": sorted(upgraded)}


def _relic_text(item: dict) -> str | None:
    try:
        return relic_text(Relic.model_validate({
            "id": item.get("id") or item.get("name") or "?",
            "name": item.get("name") or "?",
            "counter": item.get("counter", -1),
        }))
    except ValueError:
        return None


def _potion_text(item: dict) -> str | None:
    try:
        return potion_text(Potion.model_validate({
            "id": item.get("id") or item.get("name") or "?",
            "name": item.get("name") or "?",
            "can_use": bool(item.get("can_use")),
            "can_discard": bool(item.get("can_discard")),
            "requires_target": bool(item.get("requires_target")),
        }))
    except ValueError:
        return None


def _collected(
    floors: list[FloorRecord], state_key: str, gained_key: str, text_of: Any
) -> list[dict[str, Any]]:
    """Every relic/potion that passed through the run's hands, with the floor
    it arrived on (None marks starting items)."""
    items: list[dict[str, Any]] = []
    if floors:
        for raw in floors[0].entry_state.get(state_key) or []:
            name = str(raw.get("name") or "")
            if not name or name == "Potion Slot":
                continue
            items.append({"name": name, "floor": None, "text": text_of(raw)})
    for floor in floors:
        for name in getattr(floor.scorecard, gained_key):
            items.append({"name": name, "floor": floor.floor, "text": text_of({"name": name})})
    return items


# The action narration names the potion at the moment it left a slot:
# "use_potion 2 (Fire Potion)" / "discard_potion 0 (Block Potion)".
_POTION_EVENT = re.compile(r"^(use_potion|discard_potion) \d+ \((.+)\)$")

# Fairy in a Bottle is the one potion that leaves the belt with no action: it
# triggers automatically on lethal damage and is consumed. Every other potion
# departs through a use/discard action attributed above, so the belt-delta
# fallback is scoped to this one name -- a blanket "vanished = triggered" would
# misread potions an event consumed as a side effect of a choose.
_AUTO_TRIGGER_POTIONS = {"Fairy in a Bottle"}


def _belt(state: dict[str, Any]) -> list[str]:
    return [p.get("name") for p in (state.get("potions") or [])]


def _potions(
    floors: list[FloorRecord], decisions: dict[int | None, list[DecisionRecord]]
) -> list[dict[str, Any]]:
    """Collected potions with their fate: the floor each one was drunk or
    tossed on, or None for a potion still in its slot when the run ended.
    Duplicate names resolve earliest-gained-first."""
    items = _collected(floors, "potions", "potions_gained", _potion_text)
    for item in items:
        item["fate"] = None
        item["fate_floor"] = None
    for floor in floors:
        for decision in decisions.get(floor.floor, []):
            event = _POTION_EVENT.match(decision.action or "")
            if event is None:
                continue
            kind, name = event.groups()
            for item in items:
                if (
                    item["name"] == name
                    and item["fate"] is None
                    and (item["floor"] or 0) <= floor.floor
                ):
                    item["fate"] = "used" if kind == "use_potion" else "discarded"
                    item["fate_floor"] = floor.floor
                    break
    # Recover auto-triggered potions (Fairy in a Bottle) from the belt emptying,
    # since they leave no action: a still-unfated one that was in the belt at a
    # floor's entry and gone by its exit fired there.
    for item in items:
        if item["fate"] is not None or item["name"] not in _AUTO_TRIGGER_POTIONS:
            continue
        for floor in floors:
            if (floor.floor or 0) < (item["floor"] or 0):
                continue
            if item["name"] in _belt(floor.entry_state) and item["name"] not in _belt(floor.exit_state):
                item["fate"] = "triggered"
                item["fate_floor"] = floor.floor
                break
    return items


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
