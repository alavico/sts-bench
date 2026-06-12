"""Report data assembly: floors, fights, the map route, and the embedded
page stay faithful to the records they were built from."""

from sts_bench.report import build_report_data, render_html
from sts_bench.trajectory import read_records

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
