"use strict";
/* Pure renderer for the campaign payload: configurations compared over a seed
   suite. All numbers were computed before embedding (RunMetrics / aggregate);
   this script only lays them out.

   The page reads top-down: the leaderboard ranks configurations, the cost
   scatter prices them, the seed grid shows consistency game by game, the HP
   small multiples show how runs die, and two tables carry playstyle and raw
   per-run numbers. Drill-down goes through each run's `page` link to its
   single-run report, where the raw model I/O lives. */

const DATA = JSON.parse(document.getElementById("data").textContent);

const WIN_FLOOR = 51; // clearing the Act III boss; Act IV is out of scope

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

const dec1 = (v) => (v == null ? "–" : String(Math.round(v * 10) / 10));
const int = (v) => (v == null ? "–" : String(Math.round(v)));
const pct = (v) => (v == null ? "–" : Math.round(v * 100) + "%");
const secs = (ms) => (ms == null ? "–" : (ms / 1000).toFixed(1) + "s");
const money = (v) => (v == null ? "–" : "$" + v.toFixed(2));
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/* Compact magnitude: 1,451,965 -> "1.45M", 30,198 -> "30.2k". */
const compact = (v) => {
  if (v == null) return "–";
  if (v >= 1e6) return +(v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return +(v / 1e3).toFixed(1) + "k";
  return String(Math.round(v));
};

/* ---------- tooltip (hover card, same classes as the single-run page) ---------- */

const tip = el("div", { class: "tooltip" });
tip.style.display = "none";
document.body.append(tip);

function showTip(evt, node) {
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

/* ---------- config families and colors ---------- */

// A family is what deserves one color: the (model, agent, effort) triple for
// LLM configurations, the agent name for baselines. Ascensions of the same
// family share it, so the eye can follow a model across difficulty levels.
const isBaseline = (agent) => agent === "random" || agent === "scripted";
const familyOf = (c) =>
  isBaseline(c.agent) ? c.agent : `${c.model}|${c.agent}|${c.effort || ""}`;

// Hue says vendor, shade says variant: every Claude reads orange, every GPT
// teal, every Gemini blue, so a mixed leaderboard sorts itself by eye.
// Baselines are calibration, not contestants — they stay gray, and red/green
// keep meaning only "died" / "won".
const VENDOR_HUES = [
  ["claude", "var(--orange)"],
  ["gpt", "var(--series-6)"],
  ["gemini", "var(--blue)"],
  ["kimi", "var(--series-5)"],
  ["glm", "var(--series-8)"],
];
const SHADES = [
  (base) => base,
  (base) => `color-mix(in srgb, ${base} 60%, var(--text) 40%)`,
  (base) => `color-mix(in srgb, ${base} 55%, var(--bg) 45%)`,
  (base) => `color-mix(in srgb, ${base} 45%, var(--dim) 55%)`,
];
const PALETTE = ["var(--series-7)", "var(--indigo)", "var(--gold)"]; // unclaimed vendors

const FAMILY_COLOR = (() => {
  const map = new Map([
    ["scripted", "var(--dim)"],
    ["random", "color-mix(in srgb, var(--dim) 60%, var(--bg) 40%)"],
  ]);
  const models = new Map(); // family -> model, first seen
  for (const c of DATA.configs) {
    if (!isBaseline(c.agent) && !models.has(familyOf(c))) models.set(familyOf(c), c.model);
  }
  const byHue = new Map();
  for (const [family, model] of models) {
    const hit = VENDOR_HUES.find(([vendor]) => model.includes(vendor));
    if (!hit) continue;
    if (!byHue.has(hit[1])) byHue.set(hit[1], []);
    byHue.get(hit[1]).push(family);
  }
  for (const [hue, families] of byHue) {
    // Newer/bigger version strings sort last, so the flagship keeps the pure hue.
    families.sort().reverse();
    families.forEach((family, i) => map.set(family, SHADES[i % SHADES.length](hue)));
  }
  for (const family of models.keys()) {
    if (map.has(family)) continue;
    let h = 0;
    for (const ch of family) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    map.set(family, PALETTE[h % PALETTE.length]);
  }
  return map;
})();

const colorOf = (family) => FAMILY_COLOR.get(family) || "var(--dim)";

/* ---------- rows: one per (configuration × ascension) ---------- */

// A label can span ascensions (same model, harder game), so runs join their
// config group on (label, ascension). Older payloads lack run.ascension; the
// label's ascension fills in when it is unambiguous.
const SOLE_ASC = new Map();
for (const c of DATA.configs) {
  SOLE_ASC.set(c.label,
    SOLE_ASC.has(c.label) && SOLE_ASC.get(c.label) !== c.ascension ? null : c.ascension);
}
const ascOf = (run) => (run.ascension != null ? run.ascension : SOLE_ASC.get(run.config));

// With a single ascension across the whole page, "A0" on every row is noise:
// subs, chips, and the run-log column only mention ascension when it varies.
const ASCENSIONS = [...new Set(DATA.configs.map((c) => c.ascension).filter((v) => v != null))]
  .sort((a, b) => a - b);
const MULTI_ASC = ASCENSIONS.length > 1;

// n-weighted mean over the label's per-version aggregates (hash drift keeps
// them separate in the payload; a rate averages back together cleanly).
function weighted(aggs, key) {
  let sum = 0, n = 0;
  for (const a of aggs) if (a[key] != null) { sum += a[key] * a.n; n += a.n; }
  return n ? sum / n : null;
}

function configRows() {
  const groups = new Map();
  for (const c of DATA.configs) {
    const key = `${c.label}@A${c.ascension}`;
    if (!groups.has(key)) groups.set(key, { key, aggs: [], runs: [] });
    groups.get(key).aggs.push(c);
  }
  for (const run of DATA.runs) {
    const g = groups.get(`${run.config}@A${ascOf(run)}`);
    if (g) g.runs.push(run);
  }
  const rows = [];
  for (const g of groups.values()) {
    const c = g.aggs[0];
    const baseline = isBaseline(c.agent);
    const family = familyOf(c);
    const n = g.aggs.reduce((s, a) => s + a.n, 0);
    const wins = g.aggs.reduce((s, a) => s + a.wins, 0);
    const floors = g.runs.map((r) => r.floor).filter((v) => v != null);
    rows.push({
      key: g.key, label: c.label, ascension: c.ascension,
      agent: c.agent, effort: c.effort,
      model: baseline ? c.agent : c.model, baseline, color: colorOf(family),
      sub: (baseline ? "baseline" : `effort ${c.effort || "—"}`)
        + (MULTI_ASC ? ` · A${c.ascension}` : ""),
      n, wins, winRate: n ? wins / n : 0,
      meanFloor: weighted(g.aggs, "mean_floor") || 0,
      bestFloor: Math.max(...g.aggs.map((a) => a.best_floor)),
      minFloor: floors.length ? Math.min(...floors) : 0,
      maxFloor: floors.length ? Math.max(...floors) : 0,
      cost: weighted(g.aggs, "cost_per_run"),
      skip: weighted(g.aggs, "skip_rate"),
      potion: weighted(g.aggs, "potion_use_rate"),
      gold: weighted(g.aggs, "gold_spent_ratio"),
      invalid: weighted(g.aggs, "invalid_rate"),
      lookups: weighted(g.aggs, "lookups_per_decision"),
      latency: weighted(g.aggs, "latency_p50_ms"),
      ptok: weighted(g.aggs, "mean_prompt_tokens"),
      ctok: weighted(g.aggs, "mean_completion_tokens"),
      runs: g.runs,
    });
  }
  rows.sort((a, b) => b.meanFloor - a.meanFloor);
  return rows;
}

const ROWS = configRows();
const LLM = ROWS.filter((r) => !r.baseline);
const BASELINES = ROWS.filter((r) => r.baseline);
const FAMILY_OF_LABEL = new Map(DATA.configs.map((c) => [c.label, colorOf(familyOf(c))]));

// Only seeds that actually have runs, so dropped seeds don't leave dead columns.
const SEEDS = (() => {
  const seen = new Set(DATA.runs.map((r) => r.seed));
  return DATA.seeds.filter((s) => seen.has(s));
})();

const spreadOf = (r) => (r.minFloor === r.maxFloor ? String(r.maxFloor) : `${r.minFloor}–${r.maxFloor}`);
const configDot = (color) => el("span", { class: "config-dot", style: "background:" + color });
const openRun = (run) => { if (run.page) window.open(run.page, "_blank"); };

/* ---------- leaderboard metrics ---------- */

const METRICS = [
  { key: "floor", label: "Mean floor", col: "mean floor", of: (r) => r.meanFloor, max: WIN_FLOOR, fmt: dec1, floorAxis: true,
    desc: "How far each configuration climbed, averaged over its runs; dots are individual seed runs." },
  { key: "best", label: "Best floor", col: "best floor", of: (r) => r.bestFloor, max: WIN_FLOOR, fmt: int, floorAxis: true,
    desc: "Each configuration's single deepest run; dots are individual seed runs." },
  { key: "win", label: "Win rate", col: "win rate", of: (r) => r.winRate, max: 1, fmt: pct, floorAxis: false,
    desc: "The share of runs that won outright." },
  { key: "cost", label: "Cost", col: "cost / run", of: (r) => r.cost, max: null, fmt: money, floorAxis: false, lowerBetter: true,
    desc: "Average dollar cost of one run, from tokens actually spent. Lower is better." },
];

function rankedLlm(metric) {
  return [...LLM].sort((a, b) => {
    let av = metric.of(a), bv = metric.of(b);
    if (av == null) av = metric.lowerBetter ? Infinity : -Infinity;
    if (bv == null) bv = metric.lowerBetter ? Infinity : -Infinity;
    if (av !== bv) return metric.lowerBetter ? av - bv : bv - av;
    return b.meanFloor - a.meanFloor;
  });
}

/* ---------- shared state ---------- */

const store = {
  metric: METRICS[0],
  ascFilter: "all", // page-wide: every section compares like-for-like
  hpKey: null, // which configuration the HP chart shows; null = best visible
  stratSort: { key: "meanFloor", desc: true },
  runsSort: { key: "floor", desc: true },
  methodOpen: false,
};

const RERENDER = [];
const rerender = () => RERENDER.forEach((fn) => fn());

// A <section> whose content rebuilds on any state change.
function section(renderInner, cls) {
  const node = el("section", cls ? { class: cls } : {});
  const render = () => node.replaceChildren(...[renderInner()].flat(Infinity).filter(Boolean));
  RERENDER.push(render);
  render();
  return node;
}

function sectionHead(title, desc, control) {
  return el("div", { class: "sec-head" },
    el("div", {}, el("h2", {}, title), desc ? el("p", { class: "sec-desc" }, desc) : null),
    control || null);
}

function segGroup(options) {
  return el("div", { class: "seg-group" },
    options.map((o) => el("button", { class: "seg" + (o.on ? " on" : ""), onclick: o.onclick }, o.label)));
}

const ctlCol = (label, group) =>
  el("div", { class: "ctl-col" }, el("span", { class: "band-label" }, label), group);

// One ascension filter for the whole page; the control appears wherever
// mixed-ascension rows would otherwise mislead.
function ascControl() {
  if (ASCENSIONS.length <= 1) return null;
  return ctlCol("Ascension level", segGroup(
    [{ key: "all", label: "All" }, ...ASCENSIONS.map((a) => ({ key: a, label: "A" + a }))]
      .map((o) => ({
        label: o.label, on: store.ascFilter === o.key,
        onclick: () => { store.ascFilter = o.key; rerender(); },
      }))));
}

const ascMatch = (ascension) => store.ascFilter === "all" || ascension === store.ascFilter;

/* ---------- header ---------- */

const CHARACTER = (DATA.suite && DATA.suite.character)
  || (DATA.configs[0] || {}).character || "ironclad";

function dekText() {
  const approx = (name) => {
    const b = BASELINES.find((r) => r.model === name);
    return b ? `${name} (≈ floor ${Math.round(b.meanFloor)})` : null;
  };
  const calibration = [approx("random"), approx("scripted")].filter(Boolean);
  const character = CHARACTER.charAt(0).toUpperCase() + CHARACTER.slice(1);
  let dek = `Frontier models play ${character} end to end — one typed action per decision`;
  dek += calibration.length ? `, calibrated against ${calibration.join(" and ")} baselines.` : ".";
  if (ASCENSIONS.length > 1) {
    dek += " Compare like-for-like by ascension: difficulty climbs sharply with it, so the"
      + " same model that clears the spire at low ascension can stall in Act I higher up.";
  }
  return dek;
}

function header() {
  return el("header", {},
    el("div", { class: "kicker" }, "STS-Bench · Campaign Report"),
    el("h1", {}, "Can frontier LLMs climb the Spire?"),
    el("p", { class: "dek" }, dekText()),
    el("div", { class: "metaline" },
      `${plural(LLM.length, "configuration")} · ${plural(DATA.runs.length, "run")}`
      + ` · ${plural(SEEDS.length, "seed")}`
      + ` · ${CHARACTER.charAt(0).toUpperCase() + CHARACTER.slice(1)}`
      + ` · ascension ${ASCENSIONS.join(", ") || "0"}`));
}

/* ---------- leaderboard ---------- */

const FLOOR_MARKS = [
  { at: 16 / WIN_FLOOR, label: "Act I" },
  { at: 33 / WIN_FLOOR, label: "Act II" },
  { at: 1, label: `Win · ${WIN_FLOOR}` },
];

function lbTrack(row, metric, axisMax, base) {
  const v = metric.of(row);
  const width = (Math.max(0, Math.min(v == null ? 0 : v, axisMax)) / axisMax) * 100;
  return el("div", { class: "lb-track" },
    metric.floorAxis ? FLOOR_MARKS.map((m) =>
      el("span", { class: "lb-mark", style: `left:${m.at * 100}%` })) : null,
    el("span", { class: "lb-bar", style: `background:${row.color};width:${width}%` }),
    metric.floorAxis && !base ? row.runs.map((run) => el("a", {
      class: "lb-dot",
      href: run.page || null, target: run.page ? "_blank" : null,
      title: `${run.seed}: floor ${run.floor} · ${run.outcome}`,
      style: `left:${(Math.min(run.floor, WIN_FLOOR) / WIN_FLOOR) * 100}%;`
        + `background:${run.outcome === "VICTORY" ? "var(--green)" : "var(--panel)"};`
        + `border:1.5px solid ${run.outcome === "VICTORY" ? "var(--green)" : row.color}`,
    })) : null);
}

function leaderboard() {
  return section(() => {
    const metric = store.metric;
    const costMax = Math.max(...LLM.map((r) => r.cost || 0), 0.01);
    const axisMax = metric.max != null ? metric.max : costMax;
    const ranked = rankedLlm(metric).filter((r) => ascMatch(r.ascension));
    const baselines = BASELINES.filter((r) => ascMatch(r.ascension));

    const head = sectionHead("Leaderboard",
      metric.desc
      + (metric.floorAxis ? ` A run wins by clearing the Act III boss — floor ${WIN_FLOOR}.` : ""),
      el("div", { class: "sec-controls" },
        ascControl(),
        ctlCol("Metric", segGroup(METRICS.map((m) => ({
          label: m.label, on: m === metric,
          onclick: () => { store.metric = m; rerender(); },
        }))))));

    const axis = el("div", { class: "lb-row lb-axis" },
      el("div"), el("div"),
      el("div", { class: "lb-scale" }, metric.floorAxis ? FLOOR_MARKS.map((m) =>
        el("span", { style: `left:${m.at * 100}%` }, m.label)) : null),
      el("div", { class: "lb-collabel" }, metric.col));

    const entries = ranked.map((row, i) => el("div", { class: "lb-row lb-entry" },
      el("div", { class: "lb-rank" + (i === 0 ? " top" : "") }, i + 1),
      el("div", { class: "lb-name" },
        el("div", { class: "who" }, configDot(row.color), el("b", {}, row.model)),
        el("div", { class: "lb-sub" }, row.sub)),
      lbTrack(row, metric, axisMax, false),
      el("div", { class: "lb-val" },
        el("b", { style: "color:" + row.color }, metric.fmt(metric.of(row))),
        el("div", { class: "lb-meta" },
          `${row.n} run${row.n === 1 ? "" : "s"} · `
          + (metric.floorAxis ? spreadOf(row) : row.wins + "W")))));

    const band = baselines.length ? el("div", { class: "lb-base-band" },
      el("div", { class: "band-label" }, "Baselines · scale calibration"),
      [...baselines].sort((a, b) => (metric.of(b) || 0) - (metric.of(a) || 0)).map((row) =>
        el("div", { class: "lb-row lb-entry base" },
          el("div"),
          el("div", { class: "lb-name" },
            el("div", { class: "who" }, configDot(row.color), el("b", {}, row.model),
              el("span", { class: "lb-sub" }, `≈ floor ${int(row.meanFloor)}`))),
          lbTrack(row, metric, axisMax, true),
          el("div", { class: "lb-val" }, el("b", {}, metric.fmt(metric.of(row))))))) : null;

    return [head, el("div", { class: "panel-card lb-card" }, axis, entries, band)];
  });
}

/* ---------- cost vs. performance ---------- */

// One dot per configuration, hovered for its run-by-run breakdown.
function scatterTip(r) {
  const row = (k, ...v) => [el("span", { class: "k" }, k), el("span", {}, ...v)];
  return el("div", { class: "tipcard" },
    el("div", { class: "tip-head" }, configDot(r.color), el("b", {}, r.model),
      el("span", { class: "dim" }, r.sub)),
    el("div", { class: "tip-rows" },
      row("mean", `floor ${dec1(r.meanFloor)} · ${money(r.cost)}/run`),
      [...r.runs].sort((a, b) => b.floor - a.floor).map((run) =>
        row(run.seed, `floor ${run.floor} · `,
          el("b", { class: run.outcome === "VICTORY" ? "up" : run.outcome === "DEFEAT" ? "down" : "flat" },
            run.outcome.toLowerCase()),
          ` · ${money(run.cost)}`))));
}

function costScatter(rows, baselines) {
  const W = 1120, H = 400, L = 46, R = 24, T = 20, B = 46;
  const maxCost = Math.max(...rows.map((r) => r.cost)) * 1.12;
  const topFloor = WIN_FLOOR + 4; // headroom so winning dots don't hug the frame
  const px = (c) => L + (c / maxCost) * (W - L - R);
  const py = (f) => T + (1 - Math.min(f, WIN_FLOOR) / topFloor) * (H - T - B);
  const bottom = H - B;

  // Labels take the first free spot around their dot; a dot in a crowd keeps
  // only its tooltip rather than stacking text on text. Dots themselves are
  // claimed first so no label sits on top of one.
  const placed = rows.map((r) => ({ x: px(r.cost) - 8, y: py(r.meanFloor) - 7, w: 16 }));
  const fits = (b) => b.x >= L && b.x + b.w <= W - 2 && b.y >= T - 12 && placed.every(
    (p) => b.x + b.w < p.x || p.x + p.w < b.x || b.y + 11 < p.y || p.y + 11 < b.y);
  const labels = [];
  for (const r of [...rows].sort((a, b) => a.meanFloor - b.meanFloor)) {
    const w = r.model.length * 6.4;
    const cx = px(r.cost), cy = py(r.meanFloor);
    const spot = [
      { x: cx + 10, y: cy + 4 },
      { x: cx - 10 - w, y: cy + 4 },
      { x: cx - w / 2, y: cy - 11 },
      { x: cx - w / 2, y: cy + 18 },
    ].find((s) => fits({ x: s.x, y: s.y - 9, w }));
    if (!spot) continue;
    placed.push({ x: spot.x, y: spot.y - 9, w });
    labels.push(sv("text", {
      class: "scatter-label", x: spot.x.toFixed(1), y: spot.y.toFixed(1),
    }, r.model));
  }

  return sv("svg", { class: "hp-big", viewBox: `0 0 ${W} ${H}` },
    [0, 16, 33, WIN_FLOOR].map((f) => [
      sv("line", { class: "hp-grid-line", x1: L, y1: py(f).toFixed(1), x2: W - R, y2: py(f).toFixed(1) }),
      sv("text", { class: "hp-axis", x: L - 6, y: (py(f) + 3).toFixed(1), "text-anchor": "end" }, f),
    ]),
    [0.25, 0.5, 0.75, 1].map((frac) => sv("text", {
      class: "hp-axis", x: px(maxCost * frac).toFixed(1), y: bottom + 16, "text-anchor": "middle",
    }, money(maxCost * frac))),
    sv("text", { class: "hp-axis-title", x: 11, y: py(WIN_FLOOR / 2).toFixed(1), "text-anchor": "middle",
      transform: `rotate(-90 11 ${py(WIN_FLOOR / 2).toFixed(1)})` }, "FLOOR"),
    sv("text", { class: "hp-axis-title", x: ((L + W - R) / 2).toFixed(1), y: H - 4, "text-anchor": "middle" }, "COST / RUN"),
    baselines.map((b) => [
      sv("line", { class: "scatter-base", x1: L, y1: py(b.meanFloor).toFixed(1), x2: W - R, y2: py(b.meanFloor).toFixed(1) }),
      sv("text", { class: "scatter-base-label", x: W - R, y: (py(b.meanFloor) - 5).toFixed(1), "text-anchor": "end" },
        `${b.model} · floor ${int(b.meanFloor)}`),
    ]),
    rows.map((r) => sv("circle", {
      class: "scatter-dot", cx: px(r.cost).toFixed(1), cy: py(r.meanFloor).toFixed(1),
      r: 6, fill: r.color,
      onmouseenter: (e) => showTip(e, scatterTip(r)),
      onmousemove: moveTip,
      onmouseleave: hideTip,
    })),
    labels);
}

function costSection() {
  return section(() => {
    if (!LLM.some((r) => r.cost != null)) return []; // unpriced campaign: nothing to plot
    const rows = LLM.filter((r) => r.cost != null && ascMatch(r.ascension));
    const head = sectionHead("Cost vs. performance",
      "Each dot is a configuration: mean floor reached against mean dollar cost per run."
      + " Up and to the left wins — deeper climbs for fewer dollars. Hover a dot for its"
      + " individual runs; dashed lines are the free baselines.",
      ascControl());
    if (!rows.length) {
      return [head, el("p", { class: "sec-note" }, "No priced runs at this ascension.")];
    }
    const baselines = BASELINES.filter((r) => ascMatch(r.ascension));
    return [head, el("div", { class: "panel-card hp-chart-card" }, costScatter(rows, baselines))];
  });
}

/* ---------- floor by seed ---------- */

// Act-colored heat: the further the run got, the cooler and stronger the cell.
function cellBg(floor, win) {
  let color, alpha;
  if (win) { color = "var(--green)"; alpha = 0.82; }
  else if (floor > 33) { color = "var(--blue)"; alpha = 0.2 + ((floor - 33) / 18) * 0.42; }
  else if (floor > 16) { color = "var(--orange)"; alpha = 0.18 + ((floor - 16) / 17) * 0.32; }
  else { color = "var(--red)"; alpha = 0.14 + (floor / 16) * 0.3; }
  return `color-mix(in srgb, ${color} ${Math.round(alpha * 100)}%, transparent)`;
}

function seedCell(row, seed) {
  const runs = row.runs.filter((r) => r.seed === seed);
  if (!runs.length) {
    return el("td", {}, el("span", { class: "heat-cell empty", title: "no run on " + seed }, "–"));
  }
  let best = runs[0];
  for (const r of runs) if (r.floor > best.floor) best = r;
  const win = best.outcome === "VICTORY";
  return el("td", {}, el(best.page ? "a" : "span", {
    class: "heat-cell",
    href: best.page || null, target: best.page ? "_blank" : null,
    title: `${seed} · ${runs.map((r) => `floor ${r.floor} ${r.outcome}`).join(" · ")}`
      + (best.page ? " · open report" : ""),
    style: `background:${cellBg(best.floor, win)}` + (win ? ";color:#10240f" : ""),
  }, best.floor));
}

function seedGrid() {
  return section(() => {
    const rows = rankedLlm(store.metric).concat(BASELINES)
      .filter((r) => ascMatch(r.ascension));

    const head = sectionHead("Floor by seed",
      "Every configuration runs the same deterministic seeds, so spread across a row is"
      + " the model's consistency — not the luck of the draw. Each cell is the best floor"
      + " reached on that seed; click to open the run.",
      ascControl());

    const table = el("table", { class: "heat" },
      el("thead", {}, el("tr", {},
        el("th", { class: "cfg" }, "Configuration"),
        SEEDS.map((s) => el("th", { class: "seed" }, s)),
        el("th", { class: "spread" }, "Spread"))),
      el("tbody", {}, rows.map((row) => el("tr", { style: row.baseline ? "opacity:.7" : null },
        el("td", { class: "cfg" }, configDot(row.color), " ", row.model,
          el("span", { class: "sub" }, row.sub)),
        SEEDS.map((seed) => seedCell(row, seed)),
        el("td", { class: "spread" }, spreadOf(row))))));

    const legend = el("div", { class: "heat-legend" }, [
      ["var(--red)", 28, "Act I (≤16)"],
      ["var(--orange)", 32, "Act II (17–33)"],
      ["var(--blue)", 42, "Act III (34–50)"],
      ["var(--green)", 80, `Victory (${WIN_FLOOR})`],
    ].map(([color, mix, label]) => el("span", {},
      el("span", { class: "swatch", style: `background:color-mix(in srgb,${color} ${mix}%,transparent)` }),
      label)));

    return [head, el("div", { class: "panel-card table-card" }, table), legend];
  });
}

/* ---------- HP across the run: one configuration at a time ---------- */

const runPoints = (run) => {
  const pts = [];
  if (run.start && run.start.hp != null) pts.push([run.start.floor || 0, run.start.hp]);
  for (const f of run.floors) if (f.hp != null) pts.push([f.floor, f.hp]);
  return pts;
};

// One y scale whatever the configuration, so switching keeps heights comparable.
const HP_MAX = Math.ceil(Math.max(100,
  ...DATA.runs.flatMap((r) => runPoints(r).map(([, hp]) => hp))) / 10) * 10;

const outcomeColor = (o) =>
  o === "VICTORY" ? "var(--green)" : o === "DEFEAT" ? "var(--red)" : "var(--gold)";

function hpChart(cfg) {
  const W = 1120, H = 330, L = 42, R = 14, T = 26, B = 34;
  const px = (f) => L + (Math.min(Math.max(f, 0), WIN_FLOOR) / WIN_FLOOR) * (W - L - R);
  const py = (hp) => T + (1 - Math.max(0, Math.min(hp, HP_MAX)) / HP_MAX) * (H - T - B);
  const bottom = H - B;

  const points = cfg.runs.map(runPoints).filter((pts) => pts.length);
  const maxFloor = Math.max(0, ...points.map((pts) => pts[pts.length - 1][0]));
  const median = [];
  for (let f = 0; f <= maxFloor; f++) {
    const hps = points.map((pts) => pts.find(([pf]) => pf === f)).filter(Boolean).map(([, hp]) => hp);
    if (!hps.length) continue;
    hps.sort((a, b) => a - b);
    const mid = hps.length >> 1;
    const m = hps.length % 2 ? hps[mid] : (hps[mid - 1] + hps[mid]) / 2;
    median.push(`${px(f).toFixed(1)},${py(m).toFixed(1)}`);
  }

  const withPoints = cfg.runs.filter((r) => runPoints(r).length);
  return sv("svg", { class: "hp-big", viewBox: `0 0 ${W} ${H}` },
    [0, HP_MAX / 2, HP_MAX].map((hp) => [
      sv("line", { class: "hp-grid-line", x1: L, y1: py(hp).toFixed(1), x2: W - R, y2: py(hp).toFixed(1) }),
      sv("text", { class: "hp-axis", x: L - 6, y: (py(hp) + 3).toFixed(1), "text-anchor": "end" }, int(hp)),
    ]),
    [[16, "Act I boss"], [33, "Act II boss"], [50, "Act III boss"]].map(([f, name]) => [
      sv("line", { class: "hp-act", x1: px(f).toFixed(1), y1: T, x2: px(f).toFixed(1), y2: bottom }),
      sv("text", { class: "hp-axis", x: px(f).toFixed(1), y: T - 8, "text-anchor": "middle" }, name),
    ]),
    [0, 16, 33, WIN_FLOOR].map((f) =>
      sv("text", { class: "hp-axis", x: px(f).toFixed(1), y: bottom + 14, "text-anchor": "middle" }, f)),
    sv("text", { class: "hp-axis-title", x: 11, y: py(HP_MAX / 2).toFixed(1), "text-anchor": "middle",
      transform: `rotate(-90 11 ${py(HP_MAX / 2).toFixed(1)})` }, "HP"),
    sv("text", { class: "hp-axis-title", x: px(25.5).toFixed(1), y: H - 3, "text-anchor": "middle" }, "FLOOR"),
    withPoints.map((run) => sv("polyline", {
      class: "hp-run", stroke: cfg.color,
      points: runPoints(run).map(([f, hp]) => `${px(f).toFixed(1)},${py(hp).toFixed(1)}`).join(" "),
      onclick: () => openRun(run),
    }, sv("title", {}, `${run.seed} · floor ${run.floor} · ${run.outcome}`))),
    sv("polyline", { class: "hp-med", stroke: cfg.color, points: median.join(" ") }),
    withPoints.map((run) => {
      const [f, hp] = runPoints(run).pop();
      const win = run.outcome === "VICTORY";
      return sv("circle", {
        class: "hp-end", cx: px(f).toFixed(1), cy: py(hp).toFixed(1),
        r: win ? 5.5 : 3.5, fill: outcomeColor(run.outcome),
        stroke: win ? "var(--panel)" : null, "stroke-width": win ? 1.6 : null,
        onclick: () => openRun(run),
      }, sv("title", {}, `${run.seed} · floor ${f} · ${hp} HP · ${run.outcome}`));
    }));
}

function hpSection() {
  return section(() => {
    const rows = [...LLM, ...BASELINES].filter((r) => r.runs.length && ascMatch(r.ascension));
    if (!rows.length) {
      return [el("h2", {}, "Health across the run"),
        el("p", { class: "sec-note" }, "No runs at this ascension.")];
    }
    const cfg = rows.find((r) => r.key === store.hpKey) || rows[0];

    const chips = el("div", { class: "hp-select" }, rows.map((r) => el("button", {
      class: "hp-chip" + (r === cfg ? " on" : ""),
      style: r === cfg ? `border-color:${r.color};box-shadow:inset 0 0 0 1px ${r.color}` : null,
      onclick: () => { store.hpKey = r.key; rerender(); },
    }, configDot(r.color), " ", r.model,
    r.baseline && !MULTI_ASC ? null : el("span", { class: "dim" }, " " + r.sub))));

    // Every run of the chosen configuration, biggest climb first: the chart
    // shows the shapes, this row names them.
    const legend = el("div", { class: "hp-legend" },
      [...cfg.runs].sort((a, b) => b.floor - a.floor).map((run) =>
        el(run.page ? "a" : "span", {
          class: "hp-legend-item", href: run.page || null, target: run.page ? "_blank" : null,
        },
        el("span", { class: "config-dot", style: "background:" + outcomeColor(run.outcome) }),
        ` ${run.seed} · floor ${run.floor}`)));

    return [
      el("h2", {}, "Health across the run"),
      el("p", { class: "sec-note" },
        "Pick a configuration to see all of its runs on one chart: HP remaining (y) as the"
        + " run goes deeper by floor (x). The bold line is the median across its runs; each"
        + " thin line is one seed run (click to open). A line ends where the run did — ",
        el("b", { style: "color:var(--green)" }, "green"), " for a win, ",
        el("b", { style: "color:var(--red)" }, "red"), " for a death."),
      chips,
      el("div", { class: "panel-card hp-chart-card" },
        el("div", { class: "hp-head" },
          el("div", { class: "who" }, configDot(cfg.color), el("b", {}, cfg.model)),
          el("span", { class: "hp-sub" },
            `${cfg.sub} · ${cfg.n} run${cfg.n === 1 ? "" : "s"}${cfg.wins ? ` · ${cfg.wins}W` : ""}`)),
        hpChart(cfg),
        legend),
    ];
  });
}

/* ---------- sortable tables ---------- */

function sortableHead(cols, sort) {
  return el("thead", {}, el("tr", {}, cols.map((col) => el("th", {
    class: (col.left ? "left" : "") + (sort.key === col.key ? " sorted" : "") + (col.key ? "" : " plain"),
    title: col.title || null,
    onclick: col.key ? () => {
      if (sort.key === col.key) sort.desc = !sort.desc;
      else { sort.key = col.key; sort.desc = true; }
      rerender();
    } : null,
  }, col.name + (sort.key === col.key ? (sort.desc ? " ↓" : " ↑") : "")))));
}

function sortRows(rows, sort, valueOf) {
  return [...rows].sort((a, b) => {
    let av = valueOf(a, sort.key), bv = valueOf(b, sort.key);
    if (typeof av === "string" || typeof bv === "string") {
      return (sort.desc ? -1 : 1) * String(av).localeCompare(String(bv));
    }
    if (av == null) av = -Infinity;
    if (bv == null) bv = -Infinity;
    const c = av - bv;
    return sort.desc ? -c : c;
  });
}

/* ---------- strategy & cost ---------- */

const STRAT_COLS = [
  { key: "meanFloor", name: "Floor", title: "mean floor reached", cell: (r) => dec1(r.meanFloor) },
  { key: "winRate", name: "Win", title: "win rate", cell: (r) => `${r.wins}/${r.n}` },
  { key: "skip", name: "Skips", title: "card-skip rate", cell: (r) => pct(r.skip) },
  { key: "potion", name: "Potions", title: "potion-use rate", cell: (r) => pct(r.potion) },
  { key: "gold", name: "Gold spent", title: "gold spent, out of the starting purse plus earnings", cell: (r) => pct(r.gold) },
  { key: "invalid", name: "Invalid", title: "invalid decision rate", cell: (r) => pct(r.invalid) },
  { key: "lookups", name: "Lookups", title: "observation lookups per decision",
    cell: (r) => (r.lookups == null ? "–" : r.lookups.toFixed(2)) },
  { key: "latency", name: "Latency", title: "median decision latency", cell: (r) => secs(r.latency) },
  { key: "tokens", name: "Tokens", title: "mean tokens per run (in+out)",
    cell: (r) => (r.ptok == null ? "–" : `${compact(r.ptok)}+${compact(r.ctok)}`) },
  { key: "cost", name: "Cost", title: "mean dollar cost per run", cell: (r) => money(r.cost) },
];

function strategySection() {
  return section(() => {
    const sort = store.stratSort;
    const value = (r, key) => (key === "tokens" ? (r.ptok || 0) + (r.ctok || 0) : r[key]);
    const rows = sortRows(ROWS.filter((r) => ascMatch(r.ascension)), sort, value);
    return [
      el("h2", {}, "Strategy & cost"),
      el("p", { class: "sec-note" },
        "How each configuration spent its decisions, gold and tokens. Click a column to sort."),
      el("div", { class: "panel-card table-card" }, el("table", { class: "data" },
        sortableHead([{ name: "Configuration", left: true }, ...STRAT_COLS], sort),
        el("tbody", {}, rows.map((r) => el("tr", { style: r.baseline ? "opacity:.72" : null },
          el("td", { class: "left" }, configDot(r.color), " ", r.model,
            el("span", { class: "sub" }, r.sub)),
          STRAT_COLS.map((col) => el("td", {
            class: col.key === "invalid" && r.invalid > 0.04 ? "warn-val" : null,
          }, col.cell(r)))))))),
    ];
  });
}

/* ---------- run log ---------- */

const outcomeClass = (o) => "outcome " + o.toLowerCase();

const RUN_COLS = [
  { key: "config", name: "Configuration", left: true },
  { key: "seed", name: "Seed", left: true },
  ...(MULTI_ASC ? [{ key: "asc", name: "Asc", left: true }] : []),
  { key: "outcome", name: "Outcome", left: true },
  { key: "floor", name: "Floor" },
  { key: "score", name: "Score" },
  { key: "decisions", name: "Decisions" },
  { key: "invalid", name: "Invalid" },
  { key: "cost", name: "Cost" },
];

function runLog() {
  return section(() => {
    const sort = store.runsSort;
    const value = (run, key) => (key === "asc" ? ascOf(run) ?? -1 : run[key]);
    const rows = sortRows(DATA.runs.filter((run) => ascMatch(ascOf(run))), sort, value);
    const count = store.ascFilter === "all"
      ? `All ${plural(rows.length, "run")}` : `${plural(rows.length, "run")} at A${store.ascFilter}`;
    return [
      el("h2", {}, "Run log"),
      el("p", { class: "sec-note" },
        `${count}. Click a run to open its full turn-by-turn report.`),
      el("div", { class: "panel-card table-card" }, el("table", { class: "data" },
        sortableHead(RUN_COLS, sort),
        el("tbody", {}, rows.map((run) => {
          const asc = ascOf(run);
          return el("tr", {
            class: run.page ? "click" : null,
            // The whole row is the link; the inner <a> keeps middle-click and
            // copy-link working without opening twice.
            onclick: run.page ? (evt) => { if (!evt.target.closest("a")) openRun(run); } : null,
          },
            el("td", { class: "left" },
              configDot(FAMILY_OF_LABEL.get(run.config) || "var(--dim)"), " ",
              run.page ? el("a", { href: run.page, target: "_blank" }, run.config) : run.config),
            el("td", { class: "left mono-dim" }, run.seed),
            MULTI_ASC ? el("td", { class: "left mono-dim" }, asc == null ? "—" : "A" + asc) : null,
            el("td", { class: "left" }, el("span", { class: outcomeClass(run.outcome) }, run.outcome)),
            el("td", {}, int(run.floor)),
            el("td", {}, int(run.score)),
            el("td", {}, int(run.decisions)),
            el("td", {}, int(run.invalid)),
            el("td", {}, money(run.cost)));
        })))),
    ];
  });
}

/* ---------- methodology ---------- */

function methodology() {
  return section(() => {
    const open = store.methodOpen;
    const toggle = el("button", {
      class: "method-toggle",
      onclick: () => { store.methodOpen = !store.methodOpen; rerender(); },
    },
    el("span", { class: "arrow" + (open ? " open" : "") }, "▸"),
    "Methodology & caveats",
    DATA.warnings.length ? el("span", { class: "badge" }, `· ${DATA.warnings.length} drift notes`) : null);

    const panel = open ? el("div", { class: "method-panel" },
      el("p", {},
        `Wins require clearing the Act III boss (floor ${WIN_FLOOR}); Act IV and the keys are`
        + " out of scope. The two baselines calibrate the scale: ",
        el("b", {}, "random"), " picks uniformly among validator-accepted actions, ",
        el("b", {}, "scripted"), " plays the first playable card. A model has to beat scripted"
        + " before its floor count reflects strategy. Single-run configurations are early"
        + " signal — treat their numbers as anecdotes, not estimates."),
      el("p", { class: "subhead" }, "Prompt / tool-schema drift"),
      DATA.warnings.length ? [
        el("p", {},
          "Runs whose prompt or tool schemas differed are never averaged blind — where a"
          + " configuration spans more than one version, the per-version split is preserved"
          + " in the source report:"),
        DATA.warnings.map((w) => el("div", { class: "drift" }, w)),
      ] : el("p", {}, "No drift: every run in a configuration saw the same prompt and tool schemas.")) : null;

    return [toggle, panel];
  }, "method");
}

/* ---------- page ---------- */

document.getElementById("app").append(
  themeToggle(),
  el("main", {},
    header(),
    leaderboard(),
    costSection(),
    seedGrid(),
    hpSection(),
    strategySection(),
    runLog(),
    methodology(),
    el("footer", {}, "STS-Bench")));
