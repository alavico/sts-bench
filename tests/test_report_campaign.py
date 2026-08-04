"""The campaign report: many runs shaped into one comparison page."""

from __future__ import annotations

from sts_bench.report.campaign import CampaignRun, build_campaign_data, render_campaign_html

from test_runner_metrics import make_decision, make_floor, make_run, strategic_records


def model_records(run_id="model-1", seed="STSBENCH1", floor_reached=12, victory=False):
    return [
        make_decision(run_id=run_id, index=1, floor=1),
        make_floor(run_id=run_id, floor=1, gold_delta=40, exit_gold=139),
        make_floor(run_id=run_id, floor=2, gold_delta=-20, exit_gold=119),
        make_run(run_id=run_id, seed=seed, floor_reached=floor_reached, victory=victory),
    ]


def baseline_records(run_id="random-1", seed="STSBENCH1"):
    return [
        make_decision(run_id=run_id, index=1, floor=1, latency_ms=None),
        make_floor(run_id=run_id, floor=1),
        make_run(
            run_id=run_id, seed=seed, model="random", agent="random",
            prompt_hash=None, tool_hash=None, floor_reached=4,
        ),
    ]


def campaign_runs():
    return [
        CampaignRun.from_records(model_records(), page="../trajectories/model-1.html"),
        CampaignRun.from_records(
            model_records(run_id="model-2", seed="STSBENCH2", floor_reached=51, victory=True),
            page="../trajectories/model-2.html",
        ),
        CampaignRun.from_records(baseline_records()),
    ]


def test_campaign_data_groups_configs_and_links_runs():
    data = build_campaign_data(campaign_runs())
    assert data["seeds"] == ["STSBENCH1", "STSBENCH2"]  # first-seen order

    labels = [config["label"] for config in data["configs"]]
    assert labels == ["test-model", "random"]
    model = data["configs"][0]
    assert model["n"] == 2 and model["wins"] == 1
    assert model["mean_floor"] == 31.5 and model["best_floor"] == 51

    runs = data["runs"]
    assert [run["config"] for run in runs] == ["test-model", "test-model", "random"]
    assert all(run["ascension"] == 0 for run in runs)  # runs join their config on (label, ascension)
    assert runs[0]["page"] == "../trajectories/model-1.html"
    assert runs[2]["page"] is None  # no per-run page generated for this one
    assert runs[1]["outcome"] == "VICTORY"
    assert runs[0]["prompt_hash"] == "p1"
    assert runs[0]["tool_schema_hash"] == "t1"


def test_campaign_floor_series_is_small_and_conversation_free():
    data = build_campaign_data(campaign_runs())
    run = data["runs"][0]
    assert run["start"] is not None
    assert [floor["floor"] for floor in run["floors"]] == [1, 2]
    assert run["floors"][0]["gold"] == 139  # exit-state boundary numbers only
    assert "conversation" not in str(data)  # never embed the packets


def test_campaign_data_carries_strategic_metrics():
    runs = [CampaignRun.from_records(strategic_records())]
    data = build_campaign_data(runs)
    config = data["configs"][0]
    assert config["skip_rate"] == 2 / 3
    assert config["potion_use_rate"] == 0.5
    assert config["gold_spent_ratio"] == 0.6
    assert data["runs"][0]["skip_rate"] == 2 / 3


def test_campaign_data_warns_on_hash_drift():
    runs = [
        CampaignRun.from_records([make_run(run_id="a", prompt_hash="p1")]),
        CampaignRun.from_records([make_run(run_id="b", prompt_hash="p2")]),
    ]
    data = build_campaign_data(runs)
    assert len(data["configs"]) == 2
    assert len(data["warnings"]) == 1
    assert "never averaged across revisions" in data["warnings"][0]


def test_campaign_data_discloses_excluded_pilot_runs():
    excluded = [{"run_id": "pilot-1", "reason": "preliminary prompt v1"}]
    data = build_campaign_data(campaign_runs(), excluded_runs=excluded)
    assert data["excluded_runs"] == excluded


def test_campaign_cost_only_for_priced_models():
    data = build_campaign_data(campaign_runs(), pricing={"test-model": (0.25, 2.0)})
    model, random_ = data["configs"]
    assert model["cost_per_run"] is not None and model["cost_per_run"] > 0
    assert random_["cost_per_run"] is None


def test_campaign_per_run_cost_tracks_pricing():
    # The grid pivots cost per cell, so every run carries its own dollar figure.
    data = build_campaign_data(campaign_runs(), pricing={"test-model": (0.25, 2.0)})
    model_runs = [r for r in data["runs"] if r["config"] == "test-model"]
    assert model_runs and all(r["cost"] is not None and r["cost"] > 0 for r in model_runs)
    random_run = next(r for r in data["runs"] if r["config"] == "random")
    assert random_run["cost"] is None  # unpriced model -> unknown, not a guessed zero


def test_campaign_runs_have_no_cost_without_pricing():
    data = build_campaign_data(campaign_runs())
    assert all(run["cost"] is None for run in data["runs"])


def test_campaign_title_is_the_product_name():
    data = build_campaign_data(campaign_runs())
    assert data["title"] == "Slay the Spire Bench"


def test_campaign_page_is_self_contained_and_script_safe():
    data = build_campaign_data(campaign_runs())
    # a hostile run id must not be able to close the script tag
    data["runs"][0]["run_id"] = "</script><script>alert(1)<!--"
    page = render_campaign_html(data)
    assert page.startswith("<!doctype html")
    assert "Campaign Report" in page.split("</head>")[0]
    assert '"label":"test-model"' in page
    assert page.count("</script>") == 2  # the data tag and the renderer, nothing injected
    assert "http://" not in page.split("</head>")[0]  # no external assets in the head
