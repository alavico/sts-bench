"use strict";
/* Pure renderer for the campaign payload: configurations compared over a seed
   suite. All numbers were computed before embedding (RunMetrics / aggregate);
   this script only lays them out and lets you slice the cube.

   The data is a cube: (configuration × seed × metric), plus each run's floor
   trajectory. The report reads it along every axis -- a grid column compares
   configs on one (deterministic) seed, a row tracks one config across seeds,
   the metric selector pivots what fills the cells. Drill-down goes through each
   run's `page` link to its single-run report, where the raw model I/O lives. */

const DATA = JSON.parse(document.getElementById("data").textContent);

/* ---------- theme (same key as the single-run page) ---------- */

const THEME_KEY = "sts-bench-theme";

function applyTheme(name) {
  document.documentElement.dataset.theme = name;
  localStorage.setItem(THEME_KEY, name);
}

applyTheme(localStorage.getItem(THEME_KEY) || "dark");

function themeToggle() {
  const label = () =>
    document.documentElement.dataset.theme === "dark" ? "☀ Light" : "☾ Dark";
  const btn = el("button", {
    class: "theme-toggle",
    onclick: () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
      btn.textContent = label();
    },
  }, label());
  return btn;
}

/* ---------- builders ---------- */

const SVGNS = "http://www.w3.org/2000/svg";

function applyAttrs(node, attrs) {
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
}

function appendChildren(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null) continue;
    node.append(child.nodeType ? child : String(child));
  }
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  applyAttrs(node, attrs);
  appendChildren(node, children);
  return node;
}

function sv(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVGNS, tag);
  applyAttrs(node, attrs);
  appendChildren(node, children);
  return node;
}

const fmt = new Intl.NumberFormat("en-US");
const num = (v) => (v == null ? "–" : fmt.format(v));
const pct = (v) => (v == null ? "–" : Math.round(v * 100) + "%");
const dec1 = (v) => (v == null ? "–" : Math.round(v * 10) / 10);
const secs = (ms) => (ms == null ? "–" : (ms / 1000).toFixed(1) + "s");
const money = (v) => (v == null ? "–" : "$" + v.toFixed(2));

/* Compact magnitude: 1,451,965 -> "1.45M", 30,198 -> "30.2k". */
const compact = (v) => {
  if (v == null) return "–";
  if (v >= 1e6) return +(v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return +(v / 1e3).toFixed(1) + "k";
  return String(Math.round(v));
};

/* Prompt/completion split, self-documenting: "1.45M in · 30.2k out". */
const tokensText = (input, output) => `${compact(input)} in · ${compact(output)} out`;

/* Generation stamp, as recorded: "2026-06-16T16:23:38-07:00" -> "2026-06-16
   16:23" (drop seconds and the offset; the raw ISO stays in the payload). */
const genTime = (iso) => {
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : iso.replace("T", " ");
};

/* ---------- config colors + run lookup ---------- */

const PALETTE = ["#5bc0de", "#e8c860", "#e06c5b", "#7ed87e", "#b07cf0", "#f0a850", "#6f87c0", "#d87ec5"];
const CONFIG_COLOR = new Map(DATA.configs.map((c, i) => [c.label, PALETTE[i % PALETTE.length]]));
const color = (label) => CONFIG_COLOR.get(label) || "#9aa0a6";

const RUNS_OF = new Map(DATA.configs.map((c) => [c.label, []]));
for (const run of DATA.runs) (RUNS_OF.get(run.config) || []).push(run);

// One run per (config, seed) is the norm; if a config replayed a seed, the
// first is shown in the grid and all appear in the runs table.
const runAt = (label, seed) => (RUNS_OF.get(label) || []).find((r) => r.seed === seed) || null;

/* ---------- metrics: what the grid and spotlight pivot on ---------- */

// `of` pulls the value from a run; `higherBetter` orients "good" (green) on the
// heat ramp and which extreme is "best". Cost is free for token-less baselines.
const METRICS = [
  { key: "floor", label: "floor", of: (r) => r.floor, fmt: num, higherBetter: true },
  { key: "reward", label: "reward", of: (r) => r.reward, fmt: dec1, higherBetter: true },
  { key: "score", label: "score", of: (r) => r.score, fmt: num, higherBetter: true },
  { key: "tokens", label: "tokens", of: (r) => r.prompt_tokens + r.completion_tokens, fmt: compact, higherBetter: false },
  {
    key: "cost", label: "cost", fmt: money, higherBetter: false,
    of: (r) => (r.cost != null ? r.cost : (r.prompt_tokens + r.completion_tokens === 0 ? 0 : null)),
  },
];

const signed = (metric, d) =>
  d == null ? "–" : (d > 0 ? "+" : d < 0 ? "−" : "±") + metric.fmt(Math.abs(d));

/* Heat ramp red(worst) -> gold -> green(best); t already oriented to [0,1]. */
function heat(t) {
  const stops = [[224, 108, 91], [232, 200, 96], [126, 216, 126]];
  t = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(Math.floor(t), stops.length - 2);
  const f = t - i;
  const mix = stops[i].map((a, k) => Math.round(a + (stops[i + 1][k] - a) * f));
  return `rgb(${mix[0]},${mix[1]},${mix[2]})`;
}

// Map a metric value to [0,1] (1 = best) across a set of runs, orientation-aware.
function metricScale(metric, runs) {
  const vals = runs.map(metric.of).filter((v) => v != null);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  return (v) => {
    if (v == null) return null;
    if (hi === lo) return 0.5;
    const t = (v - lo) / (hi - lo);
    return metric.higherBetter ? t : 1 - t;
  };
}

// Per-config summary of a metric across that config's seeds.
function summarize(label, metric) {
  const vals = (RUNS_OF.get(label) || []).map(metric.of).filter((v) => v != null);
  if (!vals.length) return { mean: null, median: null, best: null };
  const sorted = [...vals].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return {
    mean: vals.reduce((a, b) => a + b, 0) / vals.length,
    median: sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2,
    best: metric.higherBetter ? Math.max(...vals) : Math.min(...vals),
  };
}

/* ---------- shared state ---------- */

const store = {
  metric: METRICS[0],
  selected: new Set(DATA.configs.map((c) => c.label)),
  delta: false,
  reference: null, // config label; defaults to first selected
  seed: DATA.seeds[0],
};

const RERENDER = [];
const rerender = () => RERENDER.forEach((fn) => fn());

// A <section> whose inner content rebuilds on any state change.
function section(title, renderInner, attrs = {}) {
  const holder = el("div");
  const render = () => holder.replaceChildren(...[renderInner()].flat(Infinity).filter(Boolean));
  RERENDER.push(render);
  render();
  return el("section", attrs, title ? el("h2", {}, title) : null, holder);
}

const selectedConfigs = () => DATA.configs.filter((c) => store.selected.has(c.label));
const referenceLabel = (sel) => {
  if (store.reference && store.selected.has(store.reference)) return store.reference;
  return sel.length ? sel[0].label : null;
};

/* ---------- tooltip ---------- */

const tip = el("div", { class: "tooltip" });
tip.style.display = "none";
document.body.append(tip);

function showTipNode(evt, node) {
  tip.replaceChildren(node);
  tip.style.display = "block";
  moveTip(evt);
}

function moveTip(evt) {
  const pad = 14;
  let x = evt.clientX + pad;
  let y = evt.clientY + pad;
  const rect = tip.getBoundingClientRect();
  if (x + rect.width > innerWidth - 8) x = evt.clientX - rect.width - pad;
  if (y + rect.height > innerHeight - 8) y = evt.clientY - rect.height - pad;
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}

function hideTip() {
  tip.style.display = "none";
}

function runTip(run) {
  const rows = [];
  const row = (k, v) => rows.push(el("span", { class: "k" }, k), el("span", {}, v));
  row("seed", run.seed);
  row("outcome", run.outcome + (run.floor != null ? ` on floor ${run.floor}` : ""));
  if (run.score != null) row("score", num(run.score));
  if (run.reward != null) row("reward", dec1(run.reward));
  row("decisions", `${num(run.decisions)} (${run.forced} forced, ${run.invalid} invalid)`);
  if (run.skip_rate != null) row("card skips", pct(run.skip_rate));
  if (run.potions_gained) row("potions", `${run.potions_used}/${run.potions_gained} used`);
  if (run.gold_final != null) row("gold at end", num(run.gold_final));
  if (run.prompt_tokens) row("tokens", tokensText(run.prompt_tokens, run.completion_tokens));
  if (run.cost != null) row("cost", money(run.cost));
  return el("div", { class: "tipcard" },
    el("div", { class: "tip-head" },
      el("span", { class: "config-dot", style: "background:" + color(run.config) }),
      el("b", {}, run.run_id)),
    el("div", { class: "tip-rows" }, rows),
    run.page ? el("div", { class: "dim", style: "margin-top:6px" }, "click to open the run report") : null);
}

function hoverable(node, run) {
  node.addEventListener("mousemove", (evt) => showTipNode(evt, runTip(run)));
  node.addEventListener("mouseleave", hideTip);
  if (run.page) node.addEventListener("click", () => { window.location = run.page; });
  return node;
}

const chip = (k, v) => el("span", { class: "chip" }, k + " ", el("b", {}, String(v)));

function hashLine(config) {
  const short = (h) => (h ? h.slice(0, 8) : "—");
  return `prompt ${short(config.prompt_hash)} · tools ${short(config.tool_schema_hash)}`;
}

const configDot = (label) => el("span", { class: "config-dot", style: "background:" + color(label) });

/* ---------- header ---------- */

function header() {
  const suite = DATA.suite;
  const wins = DATA.runs.filter((r) => r.outcome === "VICTORY").length;
  const subtitle = suite
    ? `${suite.character} · ascension ${suite.ascension} · ${DATA.seeds.length} seeds · ${DATA.configs.length} configurations`
    : `${DATA.configs.length} configurations · ${DATA.runs.length} runs`;
  return el("header", {},
    el("h1", {}, DATA.title),
    el("div", { class: "configline" }, subtitle),
    el("div", { class: "stats" },
      suite ? chip("suite", suite.name) : null,
      chip("runs", DATA.runs.length),
      chip("wins", wins),
      chip("generated", genTime(DATA.generated_at))),
    DATA.warnings.map((w) => el("div", { class: "warn" }, "⚠ " + w)));
}

/* ---------- results grid: the backbone ---------- */

function metricSelector() {
  return el("div", { class: "metric-select" },
    el("span", { class: "ctl-label" }, "metric"),
    METRICS.map((m) => el("button", {
      class: "seg" + (m.key === store.metric.key ? " on" : ""),
      onclick: () => { store.metric = m; rerender(); },
    }, m.label)));
}

function selectionControls(sel) {
  const all = el("button", { class: "linkish", onclick: () => { store.selected = new Set(DATA.configs.map((c) => c.label)); rerender(); } }, "all");
  const none = el("button", { class: "linkish", onclick: () => { store.selected = new Set(); rerender(); } }, "none");
  const ref = referenceLabel(sel);
  const deltaBox = el("input", { type: "checkbox", onchange: () => { store.delta = deltaBox.checked; rerender(); } });
  deltaBox.checked = store.delta;
  const refSel = el("select", {
    disabled: !store.delta || sel.length < 2 ? "" : null,
    onchange: () => { store.reference = refSel.value; rerender(); },
  }, sel.map((c) => el("option", { value: c.label }, c.label)));
  if (ref) refSel.value = ref;
  return el("div", { class: "grid-controls" },
    el("span", { class: "ctl-label" }, "select"), all, none,
    el("label", { class: "delta-toggle" }, deltaBox, " Δ vs ", refSel));
}

function gridSection() {
  return section("Results", () => {
    const metric = store.metric;
    const sel = selectedConfigs();
    const compRuns = sel.flatMap((c) => RUNS_OF.get(c.label) || []);
    const scale = metricScale(metric, compRuns);
    const ref = referenceLabel(sel);
    const refRun = (seed) => (ref ? runAt(ref, seed) : null);

    // winner per seed (best among selected) and best seed per config (any config)
    const winner = new Map();
    for (const seed of DATA.seeds) {
      let bestLabel = null, bestVal = null;
      for (const c of sel) {
        const run = runAt(c.label, seed);
        const v = run && metric.of(run);
        if (v == null) continue;
        if (bestVal == null || (metric.higherBetter ? v > bestVal : v < bestVal)) { bestVal = v; bestLabel = c.label; }
      }
      if (bestLabel) winner.set(seed, bestLabel);
    }
    const rowBest = new Map(); // config label -> seed with its best value
    for (const c of DATA.configs) {
      const best = summarize(c.label, metric).best;
      const seed = DATA.seeds.find((s) => { const r = runAt(c.label, s); return r && metric.of(r) === best; });
      if (seed != null) rowBest.set(c.label, seed);
    }
    let maxAbsDelta = 0;
    if (store.delta && ref) {
      for (const c of sel) for (const seed of DATA.seeds) {
        const run = runAt(c.label, seed), rr = refRun(seed);
        if (run && rr && c.label !== ref) maxAbsDelta = Math.max(maxAbsDelta, Math.abs(metric.of(run) - metric.of(rr)));
      }
    }

    const head = el("tr", {},
      el("th", { class: "left" }, "configuration"),
      ["mean", "median", "best"].map((s) => el("th", { class: "agg" }, s)),
      DATA.seeds.map((s) => el("th", {}, s)));

    const rows = DATA.configs.map((config) => {
      const on = store.selected.has(config.label);
      const box = el("input", { type: "checkbox", onchange: () => { on ? store.selected.delete(config.label) : store.selected.add(config.label); rerender(); } });
      box.checked = on;
      const sum = summarize(config.label, metric);
      return el("tr", { class: on ? null : "off" },
        el("td", { class: "left cfg" }, box, configDot(config.label),
          el("span", {}, config.label, el("div", { class: "hashes" }, hashLine(config)))),
        el("td", { class: "agg" }, metric.fmt(sum.mean)),
        el("td", { class: "agg" }, metric.fmt(sum.median)),
        el("td", { class: "agg" }, metric.fmt(sum.best)),
        DATA.seeds.map((seed) => gridCell(config, seed, metric, { on, scale, winner, rowBest, ref, refRun, maxAbsDelta })));
    });

    return [
      el("div", { class: "grid-bar" }, metricSelector(), selectionControls(sel)),
      el("div", { class: "dim small grid-legend" },
        "color = relative ", metric.label,
        " among selected · ★ best config on a seed · ring = config's best seed · check rows to focus the comparison · rows with different prompt/tool hashes never merge"),
      el("div", { class: "grid-scroll" }, el("table", { class: "grid" }, head, ...rows)),
    ];
  });
}

function gridCell(config, seed, metric, ctx) {
  const run = runAt(config.label, seed);
  if (!run) return el("td", {}, el("span", { class: "gcell empty" }, "–"));
  const v = metric.of(run);
  const won = run.outcome === "VICTORY";
  const isWinner = ctx.winner.get(seed) === config.label;
  const isRowBest = ctx.rowBest.get(config.label) === seed;

  let text, bg;
  if (ctx.ref && store.delta) {
    const rr = ctx.refRun(seed);
    if (config.label === ctx.ref) { text = el("span", { class: "ref-tag" }, "ref"); bg = null; }
    else if (rr && v != null) {
      const d = v - metric.of(rr);
      text = signed(metric, d);
      const better = metric.higherBetter ? d > 0 : d < 0;
      const mag = ctx.maxAbsDelta ? Math.min(1, Math.abs(d) / ctx.maxAbsDelta) : 0;
      bg = d === 0 ? null : (better ? `rgba(126,216,126,${0.2 + 0.6 * mag})` : `rgba(224,108,91,${0.2 + 0.6 * mag})`);
    } else { text = v == null ? "–" : metric.fmt(v); bg = null; }
  } else {
    text = v == null ? "?" : metric.fmt(v);
    const t = ctx.on ? ctx.scale(v) : null;
    bg = t == null ? null : heat(t);
  }

  const cls = ["gcell"];
  if (!ctx.on) cls.push("off");
  if (isWinner) cls.push("winner");
  if (isRowBest) cls.push("rowbest");
  const node = el(run.page ? "a" : "span", {
    class: cls.join(" "),
    href: run.page || null,
    style: bg ? "background:" + bg : null,
  }, text, won ? el("span", { class: "star" }, "★") : (isWinner ? el("span", { class: "star" }, "★") : null));
  node.addEventListener("mousemove", (evt) => showTipNode(evt, runTip(run)));
  node.addEventListener("mouseleave", hideTip);
  return el("td", {}, node);
}

/* ---------- per-seed spotlight ---------- */

function seedSpotlight() {
  return section("Seed spotlight", () => {
    const metric = store.metric;
    const seed = DATA.seeds.includes(store.seed) ? store.seed : DATA.seeds[0];
    const tabs = el("div", { class: "seed-tabs" }, DATA.seeds.map((s) =>
      el("button", { class: "tab" + (s === seed ? " on" : ""), onclick: () => { store.seed = s; rerender(); } }, s)));

    const entries = selectedConfigs()
      .map((c) => ({ config: c, run: runAt(c.label, seed) }))
      .filter((e) => e.run);
    entries.sort((a, b) => {
      const av = metric.of(a.run), bv = metric.of(b.run);
      if (av == null) return 1;
      if (bv == null) return -1;
      return metric.higherBetter ? bv - av : av - bv;
    });

    const ranking = el("ol", { class: "ranking" }, entries.map((e, i) => {
      const v = metric.of(e.run);
      const node = el(e.run.page ? "a" : "div", { class: "rank-card", href: e.run.page || null },
        el("span", { class: "rank-n" }, "#" + (i + 1)),
        configDot(e.config.label),
        el("span", { class: "rank-label" }, e.config.label),
        el("span", { class: "rank-val" }, v == null ? "–" : metric.fmt(v) + " " + metric.label),
        el("span", { class: "outcome " + e.run.outcome.toLowerCase() }, e.run.outcome));
      node.addEventListener("mousemove", (evt) => showTipNode(evt, runTip(e.run)));
      node.addEventListener("mouseleave", hideTip);
      return node;
    }));

    const seedRuns = entries.map((e) => e.run).filter((r) => r.floors.length);
    const overlay = seedRuns.length
      ? hpChart(seedRuns, `HP on ${seed}`, "same game for every config (seeds are deterministic) · hover a line for the run")
      : el("div", { class: "dim small" }, "no trajectories to plot for this seed");

    return [tabs, el("div", { class: "spotlight" }, ranking, overlay)];
  });
}

/* ---------- charts (selection-aware) ---------- */

const BOSSES = [[16, "act 1 boss"], [33, "act 2 boss"], [50, "act 3 boss"]];

function maxFloorShown(runs) {
  return Math.max(16, ...runs.map((r) => r.floor || 0)) + 1;
}

function bossGuides(x, height) {
  return BOSSES.filter(([floor]) => x(floor) <= x.range).map(([floor, name]) => [
    sv("line", { class: "boss-line", x1: x(floor), x2: x(floor), y1: 16, y2: height }),
    sv("text", { class: "boss-tick", x: x(floor), y: 11, "text-anchor": "middle" }, name),
  ]);
}

function axisX(left, right, maxFloor) {
  const x = (floor) => left + (floor / maxFloor) * (right - left);
  x.range = right;
  return x;
}

/* Floor reached, one dot per run: the distribution the mean hides. */
function dotPlot(configs) {
  const rowH = 26, left = 8, width = 620;
  const runs = configs.flatMap((c) => RUNS_OF.get(c.label) || []);
  const maxFloor = maxFloorShown(runs);
  const height = 24 + configs.length * rowH + 18;
  const x = axisX(left, width - 14, maxFloor);
  const svg = sv("svg", { class: "chart", viewBox: `0 0 ${width} ${height}` });
  appendChildren(svg, [bossGuides(x, height - 14)]);
  configs.forEach((config, i) => {
    const cy = 24 + i * rowH + rowH / 2;
    svg.append(sv("line", { class: "grid", x1: left, x2: width - 14, y1: cy, y2: cy }));
    (RUNS_OF.get(config.label) || []).filter((r) => r.floor != null).forEach((run, j) => {
      const dot = sv("circle", {
        class: "dot-run" + (run.outcome === "VICTORY" ? "" : " dead"),
        cx: x(run.floor), cy: cy + ((j % 3) - 1) * 5, r: 5.5, fill: color(config.label),
      });
      svg.append(hoverable(dot, run));
    });
  });
  const labels = el("div", { class: "legend-row" }, configs.map((c) =>
    el("span", {}, el("span", { class: "dot", style: "background:" + color(c.label) }), c.label)));
  return el("div", {}, el("h3", {}, "Floor reached per run"), svg, labels);
}

/* HP over floors for a set of runs, overlaid: how runs die. */
function hpChart(runs, title, sub) {
  const width = 620, height = 250, left = 30, bottom = height - 18;
  runs = runs.filter((r) => r.floors.length);
  const maxFloor = maxFloorShown(runs);
  const maxHp = Math.max(80, ...runs.flatMap((r) => r.floors.map((f) => f.max_hp || 0)));
  const x = axisX(left, width - 14, maxFloor);
  const y = (hp) => bottom - (hp / maxHp) * (bottom - 22);
  const svg = sv("svg", { class: "chart", viewBox: `0 0 ${width} ${height}` });
  for (const hp of [0, Math.round(maxHp / 2), maxHp]) {
    svg.append(
      sv("line", { class: "grid", x1: left, x2: width - 14, y1: y(hp), y2: y(hp) }),
      sv("text", { class: "axis", x: left - 4, y: y(hp) + 3, "text-anchor": "end" }, hp));
  }
  appendChildren(svg, [bossGuides(x, bottom)]);
  for (const run of runs) {
    const points = [];
    if (run.start && run.start.hp != null) points.push([run.start.floor || 0, run.start.hp]);
    for (const floor of run.floors) if (floor.hp != null) points.push([floor.floor, floor.hp]);
    if (points.length < 2) continue;
    const line = sv("polyline", {
      class: "hp-line", stroke: color(run.config),
      points: points.map(([f, hp]) => `${x(f)},${y(hp)}`).join(" "),
    });
    line.addEventListener("mouseenter", () => line.classList.add("hl"));
    line.addEventListener("mouseleave", () => line.classList.remove("hl"));
    svg.append(hoverable(line, run));
  }
  return el("div", {}, el("h3", {}, title), svg, sub ? el("div", { class: "dim small" }, sub) : null);
}

function chartsSection() {
  return section("Across the spire", () => {
    const configs = selectedConfigs();
    if (!configs.length) return el("div", { class: "dim small" }, "select a configuration to chart");
    const allRuns = configs.flatMap((c) => RUNS_OF.get(c.label) || []);
    return el("div", { class: "charts" },
      dotPlot(configs),
      hpChart(allRuns, "HP over the run", "one line per run across every seed · hover a line for the run, click to open it"));
  });
}

/* ---------- configuration details (non-pivoting stats) ---------- */

const DETAIL_COLS = [
  { key: "skip_rate", name: "card skips", fmt: pct },
  { key: "potion_use_rate", name: "potions used", fmt: pct },
  { key: "gold_spent_ratio", name: "gold spent", fmt: pct },
  { key: "invalid_rate", name: "invalid", fmt: pct },
  { key: "forced_rate", name: "forced", fmt: pct },
  { key: "lookups_per_decision", name: "lookups/dec", fmt: (v) => (v == null ? "–" : v.toFixed(2)) },
  { key: "latency_p50_ms", name: "latency p50", fmt: secs },
];

function detailsSection() {
  return section("Configuration details", () => {
    const head = el("tr", {},
      el("th", { class: "left" }, "configuration"),
      DETAIL_COLS.map((c) => el("th", {}, c.name)),
      el("th", {}, "tokens/run"), el("th", {}, "cost/run"));
    const rows = DATA.configs.map((config) => el("tr", { class: store.selected.has(config.label) ? null : "off" },
      el("td", { class: "left cfg" }, configDot(config.label), config.label),
      DETAIL_COLS.map((c) => el("td", {}, c.fmt(config[c.key]))),
      el("td", {}, tokensText(config.mean_prompt_tokens, config.mean_completion_tokens)),
      el("td", {}, money(config.cost_per_run))));
    return el("div", { class: "grid-scroll" }, el("table", { class: "compare" }, head, ...rows));
  });
}

/* ---------- runs table (selection-aware) ---------- */

function runsTable() {
  return section("Runs", () => {
    const head = el("tr", {},
      ["run", "configuration", "seed", "outcome", "floor", "score", "decisions",
        "forced", "invalid", "card skips", "tokens", "cost", "reward"].map((name, i) =>
        el("th", { class: i < 3 ? "left" : null }, name)));
    const rows = DATA.runs.filter((r) => store.selected.has(r.config)).map((run) => el("tr", {},
      el("td", { class: "left" }, run.page
        ? el("a", { href: run.page }, run.run_id)
        : el("span", { class: "no-page" }, run.run_id)),
      el("td", { class: "left" }, configDot(run.config), run.config),
      el("td", { class: "left" }, run.seed),
      el("td", {}, el("span", { class: "outcome " + run.outcome.toLowerCase() }, run.outcome)),
      el("td", {}, num(run.floor)),
      el("td", {}, num(run.score)),
      el("td", {}, num(run.decisions)),
      el("td", {}, num(run.forced)),
      el("td", {}, num(run.invalid)),
      el("td", {}, pct(run.skip_rate)),
      el("td", {}, tokensText(run.prompt_tokens, run.completion_tokens)),
      el("td", {}, money(run.cost)),
      el("td", {}, dec1(run.reward))));
    return el("table", { class: "runs" }, head, ...rows);
  });
}

/* ---------- page ---------- */

document.getElementById("app").append(
  themeToggle(),
  el("main", {},
    header(),
    gridSection(),
    seedSpotlight(),
    chartsSection(),
    detailsSection(),
    runsTable(),
    el("footer", { class: "dim small" },
      "Every number recomputable from the trajectory JSONL · run links open the single-run reports")));
