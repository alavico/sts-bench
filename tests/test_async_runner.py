"""Suite runner against a scripted fake game: jobs, records, and the report.

The fake mod answers like the real protocol over a real socket -- menu state
on ready, combat on start, menu again after any in-combat action -- so one
"run" is start -> one decision -> back at the menu, and a suite of them
exercises the whole bench path: job sequencing, seed threading, per-job
trajectory files, and the comparison report.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from pathlib import Path

import pytest

from sts_bench.env import HarnessServer
from sts_bench.replay import verify
from sts_bench.runner import RunMetrics, Suite, comparison_report
from sts_bench.runner.async_runner import BenchConfig, run_suite
from sts_bench.trajectory import DecisionRecord, RunRecord, read_records

from conftest import FakeMod

FIXTURES = Path(__file__).parent / "fixtures" / "states"


class ScriptedGame(threading.Thread):
    """The mod side of the wire, serving a minimal one-decision run per seed."""

    def __init__(self, mod: FakeMod):
        super().__init__(daemon=True)
        self.mod = mod
        self.menu = json.loads((FIXTURES / "out_of_game-1.json").read_text())
        self.combat = json.loads((FIXTURES / "none-1.json").read_text())
        self.current = self.menu
        self.seeds_started: list[str] = []

    def _send(self, state: dict) -> None:
        self.current = state
        self.mod.send_json(state)

    def run(self) -> None:
        try:
            while True:
                line = self.mod.expect_line(timeout=10)
                if line == "ready":
                    self._send(self.menu)
                elif line.startswith("start"):
                    self.seeds_started.append(line.split()[-1])
                    self._send(self.combat)
                elif line == "state":
                    self._send(self.current)
                else:  # any in-combat action ends the scripted "run"
                    self._send(self.menu)
        except (AssertionError, OSError):
            return  # harness closed the wire; the suite is over


@pytest.fixture(scope="module")
def bench_run(tmp_path_factory):
    """Run a 2-agent x 2-seed suite over the fake game; yields everything."""
    suite = Suite(name="test", character="ironclad", ascension=0, seeds=("SEED1", "SEED2"))
    config = BenchConfig(
        suite=suite, agents=["scripted", "random"], log_dir=tmp_path_factory.mktemp("bench")
    )
    notes: list[str] = []
    with HarnessServer(port=0) as server:
        mod = FakeMod(server)
        game = ScriptedGame(mod)
        game.start()
        try:
            results = asyncio.run(run_suite(config, notes.append, server=server))
        finally:
            mod.close()
    return suite, config, results, game, notes


def test_suite_runs_every_job_cleanly(bench_run):
    _, _, results, game, _ = bench_run
    assert [(r.agent, r.seed) for r in results] == [
        ("scripted", "SEED1"),
        ("scripted", "SEED2"),
        ("random", "SEED1"),
        ("random", "SEED2"),
    ]
    assert all(r.error is None and not r.fatal for r in results)
    # every job's seed reached the wire in its start command
    assert game.seeds_started == ["SEED1", "SEED2", "SEED1", "SEED2"]


def test_each_job_writes_its_own_trajectory(bench_run):
    _, _, results, _, _ = bench_run
    paths = {r.trajectory for r in results}
    assert len(paths) == len(results)
    for result in results:
        records = list(read_records(result.trajectory))
        run = next(r for r in records if isinstance(r, RunRecord))
        assert run.seed == result.seed
        assert run.agent == result.agent
        assert run.model == result.agent  # baselines record their name as the model
        assert run.prompt_hash is None  # no prompt was shown to anyone
        assert run.totals.decisions >= 1
        assert verify(records) == []  # packet property holds for empty conversations too


def test_report_renders_one_row_per_baseline(bench_run):
    suite, _, results, _, _ = bench_run
    runs = [RunMetrics.from_file(r.trajectory) for r in results]
    report = comparison_report(runs, suite=suite)
    assert "| scripted | 2 |" in report
    assert "| random | 2 |" in report
    assert "SEED1" in report and "SEED2" in report


class BrokeShopGame(threading.Thread):
    """The STSBENCH4 live failure: a shop with nothing affordable.

    SHOP_ROOM's only choice re-enters the shop; the broke SHOP_SCREEN only
    offers leave. A memoryless scripted agent cycles between them forever --
    the loop guard plus the advance-family forced fallback must walk the run
    out through SHOP_ROOM's `proceed` instead of burning the decision budget.
    """

    def __init__(self, mod: FakeMod):
        super().__init__(daemon=True)
        self.mod = mod
        self.menu = json.loads((FIXTURES / "out_of_game-1.json").read_text())
        screen = json.loads((FIXTURES / "shop_screen-1.json").read_text())
        screen["available_commands"] = ["leave", "key", "click", "wait", "state"]
        self.shop_screen = screen
        room = json.loads((FIXTURES / "shop_screen-1.json").read_text())
        room["available_commands"] = ["choose", "proceed", "key", "click", "wait", "state"]
        room["game_state"]["screen_type"] = "SHOP_ROOM"
        room["game_state"]["screen_state"] = {}
        room["game_state"]["choice_list"] = ["shop"]
        self.shop_room = room
        self.current = self.menu

    def _send(self, state: dict) -> None:
        self.current = state
        self.mod.send_json(state)

    def run(self) -> None:
        try:
            while True:
                line = self.mod.expect_line(timeout=10)
                if line == "ready":
                    self._send(self.menu)
                elif line.startswith("start"):
                    self._send(self.shop_room)
                elif line.startswith("choose"):
                    self._send(self.shop_screen)
                elif line == "leave":
                    self._send(self.shop_room)
                elif line == "proceed":
                    self._send(self.menu)
                elif line == "state":
                    self._send(self.current)
        except (AssertionError, OSError):
            return


def test_loop_guard_walks_the_run_out_of_a_broke_shop():
    suite = Suite(name="shop", character="ironclad", ascension=0, seeds=("SEED1",))
    with tempfile.TemporaryDirectory() as tmp:
        config = BenchConfig(suite=suite, agents=["scripted"], log_dir=Path(tmp))
        with HarnessServer(port=0) as server:
            mod = FakeMod(server)
            BrokeShopGame(mod).start()
            try:
                results = asyncio.run(run_suite(config, lambda _: None, server=server))
            finally:
                mod.close()
        assert len(results) == 1
        result = results[0]
        assert result.error is None and not result.fatal
        records = list(read_records(result.trajectory))
        run = next(r for r in records if isinstance(r, RunRecord))
        # the cycle broke in a handful of decisions, not at the budget
        assert run.totals.decisions < 12
        assert run.totals.forced >= 1
        commands = [r.command for r in records if isinstance(r, DecisionRecord)]
        assert "proceed" in commands  # the escape SHOP_ROOM always offered


def test_random_walk_is_reproducible_per_seed(bench_run):
    """Same rng seed, same fake game: both random jobs on SEED1-like states
    pick deterministically; the recorded commands prove the seeding wired up."""
    _, _, results, _, _ = bench_run
    by_seed = {}
    for result in results:
        if result.agent != "random":
            continue
        records = list(read_records(result.trajectory))
        by_seed[result.seed] = [
            r.command for r in records if isinstance(r, DecisionRecord)
        ]
    assert set(by_seed) == {"SEED1", "SEED2"}
