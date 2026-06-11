"""Log replay: wire states re-render through the current serializer."""

import json
from pathlib import Path

from sts_bench.render import iter_states, render_states

FIXTURES = Path(__file__).parent / "fixtures" / "states"


def wire(message):
    return f"11:03:30.082 << {json.dumps(message)}"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def make_log(tmp_path, *messages, name="play-test.log"):
    lines = [
        "11:03:30.082 -- model: test | floor agent | ironclad",
        "11:03:30.082 >> choose 1",
        wire({"error": "selected card cannot be played", "ready_for_command": True}),
        *[wire(m) for m in messages],
    ]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path


def test_log_replay_renders_each_wire_state(tmp_path):
    log = make_log(tmp_path, fixture("none-1.json"), fixture("shop_screen-1.json"))
    blocks = list(render_states(iter_states(log)))
    assert len(blocks) == 2  # the error line and non-wire lines are skipped
    assert blocks[0].startswith("--- state 1 | floor 1 | NONE ---")
    assert "<run>" in blocks[0] and "energy" in blocks[0]
    assert "--- state 2 | floor 4 | SHOP_SCREEN ---" in blocks[1]


def test_filters_select_by_screen_and_keep_stable_numbering(tmp_path):
    log = make_log(tmp_path, fixture("none-1.json"), fixture("shop_screen-1.json"))
    (block,) = render_states(iter_states(log), screen="SHOP_SCREEN")
    assert block.startswith("--- state 2")  # numbered before filtering
    (block,) = render_states(iter_states(log), index=1)
    assert "NONE" in block.splitlines()[0]


def test_deck_flag_briefs_combat_states_only(tmp_path):
    log = make_log(tmp_path, fixture("none-1.json"), fixture("shop_screen-1.json"))
    combat, shop = render_states(iter_states(log), deck=True)
    assert "<deck_reference>" in combat
    assert "<deck_reference>" not in shop


def test_unparseable_state_reports_instead_of_crashing(tmp_path):
    broken = fixture("none-1.json")
    broken["game_state"]["screen_type"] = "SOME_FUTURE_SCREEN"
    log = make_log(tmp_path, broken)
    (block,) = render_states(iter_states(log))
    assert "UNPARSED:" in block


def test_raw_json_file_renders_directly():
    blocks = list(render_states(iter_states(FIXTURES / "combat_reward-1.json")))
    assert len(blocks) == 1
    assert "loot -- take each in any order" in blocks[0]
