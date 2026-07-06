"""Report data assembly: floors, fights, the map route, and the embedded
page stay faithful to the records they were built from."""

from sts_bench.report import build_report_data, render_html
from sts_bench.trajectory import FloorScorecard, read_records
from sts_bench.trajectory.schema import RunTotals, TokenUsage

from test_replay import record_two_floor_run
from test_trajectory_schema import (
    make_decision_record,
    make_floor_record,
    make_run_record,
)

MAP_NODES = [
    {"symbol": "M", "x": 0, "y": 0, "children": [{"x": 0, "y": 1}], "parents": []},
    {"symbol": "M", "x": 2, "y": 0, "children": [{"x": 3, "y": 1}], "parents": []},
    {"symbol": "?", "x": 0, "y": 1, "children": [], "parents": []},
    {"symbol": "$", "x": 3, "y": 1, "children": [], "parents": []},
]


def map_digest(at_x: int | None, options: dict[int, int]) -> str:
    lines = ['<screen type="MAP">']
    if at_x is not None:
        lines.append(f"at node M (x={at_x})")
    lines.append("</screen>")
    lines.append("<choices>")
    lines += [f"[{i}] x={x}" for i, x in options.items()]
    lines.append("</choices>")
    return "\n".join(lines)


def make_map_floor(floor: int, digest: str, command: str, decision_index: int):
    record = make_floor_record(
        floor=floor,
        floor_type="EventRoom",
        conversation=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": digest},
            {"role": "assistant", "content": None, "tool_calls": []},
        ],
        entry_state={"floor": floor, "act": 1, "act_boss": "Hexaghost", "map": MAP_NODES},
        exit_state={"floor": floor + 1},
    )
    decision = make_decision_record(
        floor=floor,
        decision_index=decision_index,
        message_start=0,
        message_end=3,
        screen="MAP",
        action=f"choose ({command})",
        command=command,
    )
    return record, decision


def test_real_recorded_run_builds_fights_and_events(tmp_path):
    records = list(read_records(record_two_floor_run(tmp_path)))
    data = build_report_data(records)

    assert data["run"]["verdict"] == "UNFINISHED"
    assert data["system_prompt"] and "You are playing Slay the Spire" in data["system_prompt"]
    assert [f["floor"] for f in data["floors"]] == [1, 4]

    combat_floor = data["floors"][0]
    assert combat_floor["turns"] is not None
    actions = [a["text"] for t in combat_floor["turns"] for a in t["actions"]]
    assert any("play_card" in a for a in actions)
    assert any("end_turn" in a for a in actions)
    calls = [
        ev["name"]
        for d in combat_floor["decisions"]
        for ev in d["events"]
        if ev["kind"] == "call"
    ]
    assert "play_card" in calls
    # the constant system prompt is rendered once, never inside a decision
    assert all(
        "You are playing Slay the Spire" not in ev["text"]
        for f in data["floors"]
        for d in f["decisions"]
        for ev in d["events"]
    )
    assert all(f["violations"] == [] for f in data["floors"])


def test_map_route_walks_chosen_columns_row_by_row():
    floor0, d0 = make_map_floor(0, map_digest(None, {0: 0, 1: 2}), "choose 1", 1)
    floor1, d1 = make_map_floor(1, map_digest(2, {0: 3}), "choose 0", 2)
    data = build_report_data([make_run_record(), floor0, d0, floor1, d1])

    (act,) = data["acts"]
    assert act["act"] == 1 and act["boss"] == "Hexaghost"
    assert len(act["nodes"]) == len(MAP_NODES)
    assert [(p["x"], p["y"], p["floor"]) for p in act["path"]] == [(2, 0, 1), (3, 1, 2)]


def test_map_route_marks_the_boss_step():
    digest = '<screen type="MAP">\nboss fight available\n</screen>\n<choices>\n[0] boss\n</choices>'
    floor, decision = make_map_floor(15, digest, "choose 0", 1)
    data = build_report_data([floor, decision])

    (act,) = data["acts"]
    assert act["path"] == [{"boss": True, "floor": 16}]


def test_run_that_died_mid_flight_is_reported_unfinished():
    floor = make_floor_record()
    decision = make_decision_record(message_start=0, message_end=3)
    data = build_report_data([decision, floor])
    assert data["run"]["missing"] is True
    assert data["run"]["verdict"] == "UNFINISHED"
    assert data["run"]["floor_reached"] == 3


def test_rejection_tool_results_are_flagged_as_events():
    floor = make_floor_record(
        conversation=[
            {"role": "user", "content": "<state>x</state>"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "function": {"name": "play_card", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "action rejected: not enough energy"},
            {"role": "assistant", "content": "fine"},
        ]
    )
    decision = make_decision_record(message_start=0, message_end=4, invalid_actions=1)
    data = build_report_data([floor, decision])
    kinds = [ev["kind"] for ev in data["floors"][0]["decisions"][0]["events"]]
    assert kinds == ["state", "call", "rejection", "text"]


def card(name, uuid, *, cost=1, type="ATTACK", rarity="COMMON", upgrades=0):
    return {
        "id": name, "uuid": uuid, "name": name + ("+" if upgrades else ""),
        "type": type, "rarity": rarity, "cost": cost, "upgrades": upgrades,
        "has_target": False, "is_playable": None, "exhausts": False, "ethereal": False,
    }


def test_deck_is_grouped_sorted_and_carries_card_text():
    deck = [
        card("Strike", "u1"), card("Strike", "u2"),
        card("Defend", "u3", type="SKILL"),
        card("Bash", "u4", cost=2, upgrades=1),
        card("Inflame", "u5", type="POWER"),
    ]
    floor = make_floor_record(exit_state={"floor": 3, "deck": deck})
    data = build_report_data([make_run_record(), floor])

    names = [(c["name"], c["count"]) for c in data["deck"]]
    # attacks by cost, then skills, then powers
    assert names == [("Strike", 2), ("Bash+", 1), ("Defend", 1), ("Inflame", 1)]
    strike = data["deck"][0]
    assert strike["type"] == "ATTACK" and strike["cost"] == 1
    assert "damage" in strike["text"]
    bash = data["deck"][1]
    assert "Vulnerable" in bash["text"]  # the upgraded printing


def test_deck_changes_diff_by_card_identity():
    before = [card("Strike", "u1"), card("Strike", "u2"), card("Defend", "u3", type="SKILL")]
    after = [card("Strike", "u1"), card("Defend", "u3", type="SKILL", upgrades=1), card("Carnage", "u9", cost=2)]
    floor = make_floor_record(
        entry_state={"floor": 3, "deck": before},
        exit_state={"floor": 3, "deck": after},
    )
    data = build_report_data([make_run_record(), floor])

    assert data["floors"][0]["deck_changes"] == {
        "added": ["Carnage"],
        "removed": ["Strike"],
        "upgraded": ["Defend+"],
    }


def test_deck_changes_absent_when_states_lack_decks_or_nothing_changed():
    quiet = make_floor_record(
        entry_state={"floor": 3, "deck": [card("Strike", "u1")]},
        exit_state={"floor": 3, "deck": [card("Strike", "u1")]},
    )
    bare = make_floor_record(floor=4)
    data = build_report_data([make_run_record(), quiet, bare])
    assert all(f["deck_changes"] is None for f in data["floors"])
    assert data["deck"] is None  # last floor's exit state has no deck


def test_relics_and_potions_carry_acquisition_floor_and_text():
    first = make_floor_record(
        floor=1,
        entry_state={
            "floor": 1,
            "relics": [{"id": "Burning Blood", "name": "Burning Blood", "counter": -1}],
            "potions": [{"id": "Potion Slot", "name": "Potion Slot",
                         "can_use": False, "can_discard": False, "requires_target": False}],
        },
        scorecard=FloorScorecard(potions_gained=["Fire Potion"]),
    )
    second = make_floor_record(floor=2, scorecard=FloorScorecard(relics_gained=["Shuriken"]))
    data = build_report_data([make_run_record(), first, second])

    assert [(r["name"], r["floor"]) for r in data["relics"]] == [
        ("Burning Blood", None), ("Shuriken", 2)]
    assert "heal 6 HP" in data["relics"][0]["text"]
    assert "Strength" in data["relics"][1]["text"]
    # the empty slot placeholder is not a potion
    assert [(p["name"], p["floor"]) for p in data["potions"]] == [("Fire Potion", 1)]
    assert "damage" in data["potions"][0]["text"]


def test_potions_carry_their_fate_matched_earliest_gained_first():
    first = make_floor_record(
        floor=1,
        scorecard=FloorScorecard(potions_gained=["Fire Potion", "Fire Potion", "Block Potion"]),
    )
    second = make_floor_record(floor=2)
    drink = make_decision_record(
        floor=2, decision_index=1, message_start=0, message_end=0,
        action="use_potion 0 (Fire Potion)",
    )
    toss = make_decision_record(
        floor=2, decision_index=2, message_start=0, message_end=0,
        action="discard_potion 0 (Fire Potion)",
    )
    data = build_report_data([make_run_record(), first, second, drink, toss])

    assert [(p["name"], p["fate"], p["fate_floor"]) for p in data["potions"]] == [
        ("Fire Potion", "used", 2),
        ("Fire Potion", "discarded", 2),
        ("Block Potion", None, None),
    ]
    # relics have no fate: only potions get consumed
    assert all("fate" not in r for r in data["relics"])


def test_potion_used_without_a_recorded_gain_still_appears():
    # Entropic Brew fills the belt mid-fight; a product drunk on the same
    # floor never crosses a boundary snapshot, so its use action is the only
    # trace it existed. It must still show up in the potion list.
    floor = make_floor_record(floor=1)
    drink = make_decision_record(
        floor=1, decision_index=1, message_start=0, message_end=0,
        action="use_potion 0 (Attack Potion)",
    )
    data = build_report_data([make_run_record(), floor, drink])
    assert [(p["name"], p["floor"], p["fate"], p["fate_floor"]) for p in data["potions"]] == [
        ("Attack Potion", 1, "used", 1),
    ]


def test_campaign_link_rides_the_payload():
    records = [make_run_record()]
    assert build_report_data(records)["campaign"] is None
    data = build_report_data(records, campaign="../bench-smoke.html")
    assert data["campaign"] == "../bench-smoke.html"


def test_fairy_in_a_bottle_reads_as_triggered_from_the_belt_emptying():
    # Fairy auto-fires on lethal damage with no use_potion action; its fate must
    # come from the belt going empty, not from a (never-emitted) action.
    fairy = [{"name": "Fairy in a Bottle"}]
    gained = make_floor_record(
        floor=1,
        scorecard=FloorScorecard(potions_gained=["Fairy in a Bottle"]),
        exit_state={"floor": 2, "potions": fairy},
    )
    carried = make_floor_record(
        floor=2, entry_state={"floor": 2, "potions": fairy}, exit_state={"floor": 3, "potions": fairy},
    )
    died = make_floor_record(
        floor=3, entry_state={"floor": 3, "potions": fairy}, exit_state={"floor": 4, "potions": []},
    )
    data = build_report_data([make_run_record(), gained, carried, died])
    assert [(p["name"], p["fate"], p["fate_floor"]) for p in data["potions"]] == [
        ("Fairy in a Bottle", "triggered", 3),
    ]


def test_unconsumed_fairy_still_reads_as_unused():
    # Still in the belt at the end -> no trigger, fate stays None ("never used").
    fairy = [{"name": "Fairy in a Bottle"}]
    gained = make_floor_record(
        floor=1,
        scorecard=FloorScorecard(potions_gained=["Fairy in a Bottle"]),
        exit_state={"floor": 2, "potions": fairy},
    )
    end = make_floor_record(
        floor=2, entry_state={"floor": 2, "potions": fairy}, exit_state={"floor": 3, "potions": fairy},
    )
    data = build_report_data([make_run_record(), gained, end])
    assert data["potions"][0]["fate"] is None


def test_run_cost_credits_cached_tokens_at_the_reduced_rate():
    # 1M prompt of which 0.8M cached, 0.1M completion, at $5 in / $30 out.
    # cache-aware: 0.2M*5 + 0.8M*5*0.1 + 0.1M*30 = 1.0 + 0.4 + 3.0 = $4.40
    # (the naive 1M*5 + 0.1M*30 = $8.00 overstates it).
    totals = RunTotals(
        decisions=1,
        usage=TokenUsage(prompt_tokens=1_000_000, cache_read_tokens=800_000, completion_tokens=100_000),
    )
    run = make_run_record(model="gpt-5.5", totals=totals)
    data = build_report_data([run, make_floor_record()], {"gpt-5.5": (5.0, 30.0)})
    assert round(data["run"]["cost"], 2) == 4.40


def test_run_cost_uses_geminis_shallower_cache_discount():
    # Same tokens as above, but Gemini's implicit cache only discounts 75%, so a
    # hit bills at 0.25x not 0.1x: 0.2M*5 + 0.8M*5*0.25 + 0.1M*30 = 1.0 + 1.0 + 3.0
    # = $5.00 -- charging it at the Anthropic/OpenAI 0.1x would understate by $0.60.
    totals = RunTotals(
        decisions=1,
        usage=TokenUsage(prompt_tokens=1_000_000, cache_read_tokens=800_000, completion_tokens=100_000),
    )
    run = make_run_record(model="gemini-3.5-flash", totals=totals)
    data = build_report_data([run, make_floor_record()], {"gemini-3.5-flash": (5.0, 30.0)})
    assert round(data["run"]["cost"], 2) == 5.00


def test_run_cost_is_none_for_an_unpriced_model():
    run = make_run_record(model="some-unlisted-model")
    data = build_report_data([run, make_floor_record()], {"gpt-5.5": (5.0, 30.0)})
    assert data["run"]["cost"] is None
    # and with no pricing at all
    assert build_report_data([run, make_floor_record()])["run"]["cost"] is None


def test_rendered_page_is_self_contained_and_script_safe(tmp_path):
    records = list(read_records(record_two_floor_run(tmp_path)))
    data = build_report_data(records)
    # a hostile conversation string must not be able to close the script tag
    data["floors"][0]["decisions"][0]["events"].append(
        {"role": "user", "kind": "state", "text": "</script><script>alert(1)<!--"}
    )
    page = render_html(data)
    assert page.startswith("<!doctype html")
    assert '"verdict":"UNFINISHED"' in page
    assert page.count("</script>") == 2  # the data tag and the renderer, nothing injected
    assert "http://" not in page.split("</head>")[0]  # no external assets in the head
