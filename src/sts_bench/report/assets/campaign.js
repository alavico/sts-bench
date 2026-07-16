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
const EXCLUDED_RUNS = DATA.excluded_runs || [];

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
    "aria-label": "Toggle light and dark theme",
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

/* ---------- rows: one per exact benchmark configuration ---------- */

// A label can span ascensions and prompt revisions. The payload deliberately
// keeps those revisions separate; the campaign does too, so every headline
// number describes runs that saw the same prompt and tool schema.
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

const setupKey = (c) => `${c.label}@A${c.ascension}`;
const versionKey = (c) => `${c.prompt_hash || "—"}|${c.tool_schema_hash || "—"}`;
const exactKey = (c) => `${setupKey(c)}#${versionKey(c)}`;

const CONFIGS_BY_SETUP = new Map();
for (const c of DATA.configs) {
  const key = setupKey(c);
  if (!CONFIGS_BY_SETUP.has(key)) CONFIGS_BY_SETUP.set(key, []);
  CONFIGS_BY_SETUP.get(key).push(c);
}

function revisionOf(c) {
  const versions = CONFIGS_BY_SETUP.get(setupKey(c)) || [];
  if (versions.length <= 1) return null;
  return versions.findIndex((v) => versionKey(v) === versionKey(c)) + 1;
}

function runExactKey(run) {
  const setup = `${run.config}@A${ascOf(run)}`;
  const versions = CONFIGS_BY_SETUP.get(setup) || [];
  if (run.prompt_hash != null || run.tool_schema_hash != null) {
    return `${setup}#${versionKey(run)}`;
  }
  // Backward compatibility for older embedded payloads that predate hashes
  // on individual runs: a sole version is still unambiguous.
  return versions.length === 1 ? exactKey(versions[0]) : null;
}

function configRows() {
  const runsByConfig = new Map();
  for (const run of DATA.runs) {
    const key = runExactKey(run);
    if (key == null) continue;
    if (!runsByConfig.has(key)) runsByConfig.set(key, []);
    runsByConfig.get(key).push(run);
  }
  const rows = DATA.configs.map((c) => {
    const key = exactKey(c);
    const baseline = isBaseline(c.agent);
    const family = familyOf(c);
    const runs = runsByConfig.get(key) || [];
    const floors = runs.map((r) => r.floor).filter((v) => v != null);
    const revision = revisionOf(c);
    return {
      key, label: c.label, ascension: c.ascension,
      agent: c.agent, effort: c.effort,
      model: baseline ? c.agent : c.model, baseline, color: colorOf(family),
      sub: (baseline ? "baseline" : `effort ${c.effort || "—"}`)
        + (MULTI_ASC ? ` · A${c.ascension}` : "")
        + (revision ? ` · revision ${revision}` : ""),
      revision,
      n: c.n, wins: c.wins, winRate: c.n ? c.wins / c.n : 0,
      meanFloor: c.mean_floor || 0,
      bestFloor: c.best_floor,
      minFloor: floors.length ? Math.min(...floors) : 0,
      maxFloor: floors.length ? Math.max(...floors) : 0,
      cost: c.cost_per_run,
      skip: c.skip_rate,
      potion: c.potion_use_rate,
      gold: c.gold_spent_ratio,
      invalid: c.invalid_rate,
      lookups: c.lookups_per_decision,
      latency: c.latency_p50_ms,
      ptok: c.mean_prompt_tokens,
      ctok: c.mean_completion_tokens,
      runs,
    };
  });
  rows.sort((a, b) => b.meanFloor - a.meanFloor);
  return rows;
}

const ROWS = configRows();
const LLM = ROWS.filter((r) => !r.baseline);
const BASELINES = ROWS.filter((r) => r.baseline);
const ROW_BY_KEY = new Map(ROWS.map((r) => [r.key, r]));
const FAMILY_OF_LABEL = new Map(DATA.configs.map((c) => [c.label, colorOf(familyOf(c))]));

// Only seeds that actually have runs, so dropped seeds don't leave dead columns.
const SEEDS = (() => {
  const seen = new Set(DATA.runs.map((r) => r.seed));
  return DATA.seeds.filter((s) => seen.has(s));
})();

const spreadOf = (r) => r.n < 2 ? "—" : (r.minFloor === r.maxFloor ? String(r.maxFloor) : `${r.minFloor}–${r.maxFloor}`);
const configDot = (color) => el("span", { class: "config-dot", style: "background:" + color });
const openRun = (run) => { if (run.page) window.open(run.page, "_blank"); };
const openRunFromKey = (evt, run) => {
  if (evt.key === "Enter" || evt.key === " ") {
    evt.preventDefault();
    openRun(run);
  }
};

/* ---------- leaderboard metrics ---------- */

const METRICS = [
  { key: "floor", label: "Mean floor", col: "mean floor", of: (r) => r.meanFloor, max: WIN_FLOOR, fmt: (v) => dec1(v), floorAxis: true,
    desc: "How far each configuration climbed, averaged over its runs; dots are individual seed runs." },
  { key: "best", label: "Best floor", col: "best floor", of: (r) => r.bestFloor, max: WIN_FLOOR, fmt: (v) => int(v), floorAxis: true,
    desc: "Each configuration's single deepest run; dots are individual seed runs." },
  { key: "win", label: "Wins", col: "wins / runs", of: (r) => r.winRate, max: 1,
    fmt: (_v, r) => `${r.wins}/${r.n}`, floorAxis: false,
    desc: "Outright wins out of attempted runs; counts stay visible because most configurations have limited replication." },
  { key: "cost", label: "Cost", col: "cost / run · lower is better", of: (r) => r.cost, max: null,
    fmt: (v) => money(v), floorAxis: false, lowerBetter: true,
    desc: "Average estimated cost of an attempted run, from tokens actually spent. Longer bars mean lower cost." },
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

const DEFAULT_ASC = ASCENSIONS.map((asc) => ({
  asc,
  count: LLM.filter((r) => r.ascension === asc).reduce((sum, r) => sum + r.n, 0),
})).sort((a, b) => b.count - a.count || a.asc - b.asc)[0]?.asc ?? "all";

const store = {
  metric: METRICS[0],
  ascFilter: DEFAULT_ASC,
  hpKey: null,
  stratView: "behavior",
  stratSort: { key: "meanFloor", desc: true },
  runsSort: { key: "floor", desc: true },
  methodOpen: false,
};

const RERENDER = [];
const rerender = () => RERENDER.forEach((fn) => fn());

// A <section> whose content rebuilds on any state change.
function section(renderInner, cls, id) {
  const node = el("section", {
    class: cls || null,
    id: id || null,
  });
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

function segGroup(options, label) {
  return el("div", { class: "seg-group", role: "group", "aria-label": label || null },
    options.map((o) => el("button", {
      class: "seg" + (o.on ? " on" : ""),
      "aria-pressed": o.on ? "true" : "false",
      onclick: o.onclick,
    }, o.label)));
}

const ctlCol = (label, group) =>
  el("div", { class: "ctl-col" }, el("span", { class: "band-label" }, label), group);

// One ascension filter for the whole page; the control appears wherever
// mixed-ascension rows would otherwise mislead.
function ascControl() {
  if (ASCENSIONS.length <= 1) return null;
  return ctlCol("Compare ascension", segGroup(
    [...ASCENSIONS.map((a) => ({ key: a, label: "A" + a })), { key: "all", label: "Overview" }]
      .map((o) => ({
        label: o.label, on: store.ascFilter === o.key,
        onclick: () => { store.ascFilter = o.key; rerender(); },
      })), "Ascension level"));
}

const ascMatch = (ascension) => store.ascFilter === "all" || ascension === store.ascFilter;

function contextBar() {
  return section(() => el("div", { class: "context-inner" },
    el("nav", { class: "jump-nav", "aria-label": "Report sections" },
      [["#leaderboard", "Results"], ["#seeds", "Seeds"], ["#cost", "Cost"],
        ["#health", "Health"], ["#behavior", "Behavior"], ["#runs", "Runs"],
        ["#methodology", "Methodology"]]
        .map(([href, label]) => el("a", { href }, label))),
    ascControl()), "context-bar", "controls");
}

/* ---------- header ---------- */

const CHARACTER = (DATA.suite && DATA.suite.character)
  || (DATA.configs[0] || {}).character || "ironclad";

function header() {
  const character = CHARACTER.charAt(0).toUpperCase() + CHARACTER.slice(1);
  return el("header", {},
    el("h1", {}, "Slay the Spire LLM Benchmark"),
    el("p", { class: "header-dek" },
      "STS-Bench measures how well frontier language models can play ",
      el("a", { href: "https://www.megacrit.com/", target: "_blank", rel: "noreferrer" },
        "Slay the Spire"),
      ". At each decision point, a model receives a text representation of the current game"
      + " state and selects an action. Conversation context is retained during combat and"
      + " reset between floors."),
    el("p", { class: "header-findings" },
      "Because of budget constraints, most model configurations were evaluated in a single"
      + " Ironclad run on one fixed seed. These early results show that the strongest tested"
      + " models can win at Ascension 10, while smaller models and lower reasoning settings"
      + " tended to reach lower floors."),
    el("div", { class: "metaline" },
      `${character} only · Ascension ${ASCENSIONS.join(", ") || "0"}`
      + ` · ${plural(DATA.runs.length, "run")} · ${plural(SEEDS.length, "fixed seed")}`));
}

/* ---------- leaderboard ---------- */

const FLOOR_MARKS = [
  { at: 16 / WIN_FLOOR, label: "Act I" },
  { at: 33 / WIN_FLOOR, label: "Act II" },
  { at: 1, label: `Win · ${WIN_FLOOR}` },
];

function lbTrack(row, metric, axisMax, base) {
  const v = metric.of(row);
  const ratio = Math.max(0, Math.min(v == null ? 0 : v, axisMax)) / axisMax;
  const width = (metric.lowerBetter ? 1 - ratio : ratio) * 100;
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

function leaderboardCard(rows, baselines, metric, title) {
  const priced = rows.map((r) => metric.of(r)).filter((v) => v != null);
  const axisMax = metric.max != null ? metric.max : Math.max(...priced, 0.01);
  const axis = el("div", { class: "lb-row lb-axis" },
    el("div"), el("div"),
    el("div", { class: "lb-scale" }, metric.floorAxis ? FLOOR_MARKS.map((m) =>
      el("span", { style: `left:${m.at * 100}%` }, m.label)) : null),
    el("div", { class: "lb-collabel" }, metric.col));

  let priorValue;
  let priorRank = 0;
  const entries = rows.map((row, i) => {
    const value = metric.of(row);
    const rank = i > 0 && value === priorValue ? priorRank : i + 1;
    priorValue = value;
    priorRank = rank;
    return el("div", { class: "lb-row lb-entry" },
      el("div", { class: "lb-rank" + (rank === 1 ? " top" : "") }, value == null ? "—" : rank),
      el("div", { class: "lb-name" },
        el("div", { class: "who" }, configDot(row.color), el("b", {}, row.model)),
        el("div", { class: "lb-sub" }, row.sub, " ",
          el("span", { class: "sample-badge" + (row.n === 1 ? " single" : "") }, `n=${row.n}`))),
      lbTrack(row, metric, axisMax, false),
      el("div", { class: "lb-val" },
        el("b", { style: "color:" + row.color }, metric.fmt(value, row)),
        el("div", { class: "lb-meta" },
          metric.floorAxis ? `observed ${row.n < 2 ? int(row.bestFloor) : spreadOf(row)}` : `${row.wins}W`)));
  });

  const band = baselines.length ? el("div", { class: "lb-base-band" },
    el("div", { class: "band-label" }, "Baselines · scale calibration"),
    [...baselines].sort((a, b) => (metric.of(b) || 0) - (metric.of(a) || 0)).map((row) =>
      el("div", { class: "lb-row lb-entry base" },
        el("div"),
        el("div", { class: "lb-name" },
          el("div", { class: "who" }, configDot(row.color), el("b", {}, row.model),
            el("span", { class: "lb-sub" }, `n=${row.n}`))),
        lbTrack(row, metric, axisMax, true),
        el("div", { class: "lb-val" }, el("b", {}, metric.fmt(metric.of(row), row)))))) : null;

  return el("div", { class: "lb-group" },
    title ? el("h3", { class: "lb-group-title" }, title) : null,
    el("div", { class: "panel-card lb-card" }, axis, entries, band));
}

function leaderboard() {
  return section(() => {
    const metric = store.metric;
    const head = sectionHead("Leaderboard",
      metric.desc
      + (metric.floorAxis ? ` Victory means clearing the Act III boss at floor ${WIN_FLOOR}.` : "")
      + (store.ascFilter === "all" ? " Overview mode keeps ascensions in separate ranking groups." : ""),
      ctlCol("Metric", segGroup(METRICS.map((m) => ({
          label: m.label, on: m === metric,
          onclick: () => { store.metric = m; rerender(); },
        })), "Leaderboard metric")));

    const groups = (store.ascFilter === "all" ? ASCENSIONS : [store.ascFilter]).map((asc) => {
      const ranked = rankedLlm(metric).filter((r) => r.ascension === asc);
      const baselines = BASELINES.filter((r) => r.ascension === asc);
      return leaderboardCard(ranked, baselines, metric, store.ascFilter === "all" ? `Ascension ${asc}` : null);
    });
    return [head, groups];
  }, null, "leaderboard");
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
  const minCost = Math.max(0.25, Math.min(...rows.map((r) => r.cost)) * 0.8);
  const maxCost = Math.max(...rows.map((r) => r.cost)) * 1.12;
  const topFloor = WIN_FLOOR + 4; // headroom so winning dots don't hug the frame
  const logMin = Math.log(minCost), logMax = Math.log(maxCost);
  const px = (c) => L + ((Math.log(c) - logMin) / (logMax - logMin)) * (W - L - R);
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
    const label = r.model + (r.revision ? ` · rev ${r.revision}` : "");
    const w = label.length * 6.4;
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
    }, label));
  }

  const costTicks = [0.5, 1, 2, 5, 10, 20, 50].filter((v) => v >= minCost && v <= maxCost);
  return sv("svg", {
    class: "hp-big", viewBox: `0 0 ${W} ${H}`,
    role: "img", "aria-label": "Mean floor reached versus estimated cost per run on a logarithmic cost scale",
  },
    [0, 16, 33, WIN_FLOOR].map((f) => [
      sv("line", { class: "hp-grid-line", x1: L, y1: py(f).toFixed(1), x2: W - R, y2: py(f).toFixed(1) }),
      sv("text", { class: "hp-axis", x: L - 6, y: (py(f) + 3).toFixed(1), "text-anchor": "end" }, f),
    ]),
    costTicks.map((cost) => [
      sv("line", { class: "hp-grid-line", x1: px(cost).toFixed(1), y1: T, x2: px(cost).toFixed(1), y2: bottom }),
      sv("text", { class: "hp-axis", x: px(cost).toFixed(1), y: bottom + 16, "text-anchor": "middle" }, money(cost)),
    ]),
    sv("text", { class: "hp-axis-title", x: 11, y: py(WIN_FLOOR / 2).toFixed(1), "text-anchor": "middle",
      transform: `rotate(-90 11 ${py(WIN_FLOOR / 2).toFixed(1)})` }, "FLOOR"),
    sv("text", { class: "hp-axis-title", x: ((L + W - R) / 2).toFixed(1), y: H - 4, "text-anchor": "middle" }, "COST / RUN · LOG SCALE"),
    baselines.map((b) => [
      sv("line", { class: "scatter-base", x1: L, y1: py(b.meanFloor).toFixed(1), x2: W - R, y2: py(b.meanFloor).toFixed(1) }),
      sv("text", { class: "scatter-base-label", x: W - R, y: (py(b.meanFloor) - 5).toFixed(1), "text-anchor": "end" },
        `${b.model} · floor ${int(b.meanFloor)}`),
    ]),
    rows.map((r) => sv("circle", {
      class: "scatter-dot" + (r.n === 1 ? " single" : ""),
      style: `--dot-color:${r.color}`,
      cx: px(r.cost).toFixed(1), cy: py(r.meanFloor).toFixed(1),
      r: Math.min(10, 6 + Math.max(0, r.n - 1) * 1.2),
      "aria-hidden": "true",
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
    const generated = new Date(DATA.generated_at).toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric",
    });
    const head = sectionHead("Cost per run vs. mean floor",
      "Each point is one exact configuration. Up and to the left is better: deeper climbs"
      + " for fewer dollars. The logarithmic cost axis keeps lower-cost models legible;"
      + " hollow points are n=1 and point size increases with replication.");
    if (!rows.length) {
      return [head, el("p", { class: "sec-note" }, "No priced runs at this ascension.")];
    }
    const baselines = BASELINES.filter((r) => ascMatch(r.ascension));
    const key = el("div", { class: "scatter-key", "aria-label": "Cost chart values" },
      [...rows].sort((a, b) => b.meanFloor - a.meanFloor || a.cost - b.cost).map((r) =>
        el("div", { class: "scatter-key-item" },
          configDot(r.color),
          el("span", { class: "scatter-key-name" }, r.model,
            MULTI_ASC ? ` · A${r.ascension}` : "", r.revision ? ` · rev ${r.revision}` : ""),
          el("span", { class: "scatter-key-value" }, `floor ${dec1(r.meanFloor)} · ${money(r.cost)} · n=${r.n}`))));
    return [head,
      el("div", { class: "panel-card hp-chart-card" },
        el("div", { class: "chart-scroll" }, costScatter(rows, baselines)), key),
      el("p", { class: "pricing-note" },
        `Estimated cost of an attempted run using the pricing configured when this report was generated ${generated}.`)];
  }, null, "cost");
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
  return el("td", {}, el("div", { class: "heat-cell-group" },
    [...runs].sort((a, b) => b.floor - a.floor).map((run) => {
      const win = run.outcome === "VICTORY";
      return el(run.page ? "a" : "span", {
        class: "heat-cell",
        href: run.page || null, target: run.page ? "_blank" : null,
        rel: run.page ? "noreferrer" : null,
        title: `${seed} · floor ${run.floor} · ${run.outcome}` + (run.page ? " · open report" : ""),
        "aria-label": `${seed}, floor ${run.floor}, ${run.outcome.toLowerCase()}`,
        style: `background:${cellBg(run.floor, win)}` + (win ? ";color:#10240f" : ""),
      }, run.floor);
    })));
}

function seedGrid() {
  return section(() => {
    const rows = rankedLlm(METRICS[0]).concat(BASELINES)
      .filter((r) => ascMatch(r.ascension));

    const head = sectionHead("Results by fixed seed",
      "Each value is one observed run on a deterministic benchmark seed. A dash means that"
      + " configuration was not run on that seed; multiple values remain visible when a configuration was repeated on the"
      + " same seed. Select a result to open its turn-by-turn report.");

    const table = el("table", { class: "heat" },
      el("thead", {}, el("tr", {},
        el("th", { class: "cfg" }, "Configuration"),
        SEEDS.map((s, i) => el("th", { class: "seed", title: s }, `Seed ${i + 1}`)),
        el("th", { class: "spread" }, "Observed range"))),
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
  }, null, "seeds");
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
  if (points.length >= 3) {
    for (let f = 0; f <= maxFloor; f++) {
      const hps = points.map((pts) => pts.find(([pf]) => pf === f)).filter(Boolean).map(([, hp]) => hp);
      if (!hps.length) continue;
      hps.sort((a, b) => a - b);
      const mid = hps.length >> 1;
      const m = hps.length % 2 ? hps[mid] : (hps[mid - 1] + hps[mid]) / 2;
      median.push(`${px(f).toFixed(1)},${py(m).toFixed(1)}`);
    }
  }

  const withPoints = cfg.runs.filter((r) => runPoints(r).length);
  return sv("svg", {
    class: "hp-big", viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Health after each floor for ${cfg.model}, ${cfg.n} run${cfg.n === 1 ? "" : "s"}`,
  },
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
      tabindex: run.page ? 0 : null, role: run.page ? "link" : null,
      "aria-label": run.page ? `${run.seed}, floor ${run.floor}, ${run.outcome.toLowerCase()}; open report` : null,
      onclick: () => openRun(run),
      onkeydown: (evt) => openRunFromKey(evt, run),
    }, sv("title", {}, `${run.seed} · floor ${run.floor} · ${run.outcome}`))),
    median.length ? sv("polyline", { class: "hp-med", stroke: cfg.color, points: median.join(" "), "aria-hidden": "true" }) : null,
    withPoints.map((run) => {
      const [f, hp] = runPoints(run).pop();
      const win = run.outcome === "VICTORY";
      return sv("circle", {
        class: "hp-end", cx: px(f).toFixed(1), cy: py(hp).toFixed(1),
        r: win ? 5.5 : 3.5, fill: outcomeColor(run.outcome),
        stroke: win ? "var(--panel)" : null, "stroke-width": win ? 1.6 : null,
        tabindex: run.page ? 0 : null, role: run.page ? "link" : null,
        "aria-label": run.page ? `${run.seed}, floor ${f}, ${hp} HP, ${run.outcome.toLowerCase()}; open report` : null,
        onclick: () => openRun(run),
        onkeydown: (evt) => openRunFromKey(evt, run),
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
    const cfg = rows.find((r) => r.key === store.hpKey)
      || [...rows].sort((a, b) => b.n - a.n || b.meanFloor - a.meanFloor)[0];

    const chips = el("div", { class: "hp-select" }, rows.map((r) => el("button", {
      class: "hp-chip" + (r === cfg ? " on" : ""),
      style: r === cfg ? `border-color:${r.color};box-shadow:inset 0 0 0 1px ${r.color}` : null,
      "aria-pressed": r === cfg ? "true" : "false",
      onclick: () => { store.hpKey = r.key; rerender(); },
    }, configDot(r.color), " ", r.model,
    r.baseline && !MULTI_ASC ? null : el("span", { class: "dim" }, " " + r.sub))));

    // Every run of the chosen configuration, biggest climb first: the chart
    // shows the shapes, this row names them.
    const legend = el("div", { class: "hp-legend" },
      [...cfg.runs].sort((a, b) => b.floor - a.floor).map((run) =>
        el(run.page ? "a" : "span", {
          class: "hp-legend-item", href: run.page || null, target: run.page ? "_blank" : null,
          rel: run.page ? "noreferrer" : null,
        },
        el("span", { class: "outcome-symbol " + run.outcome.toLowerCase() },
          run.outcome === "VICTORY" ? "✓" : run.outcome === "DEFEAT" ? "×" : "■"),
        ` ${run.seed} · floor ${run.floor} · ${run.outcome.toLowerCase()}`)));

    return [
      el("h2", {}, "Health across the run"),
      el("p", { class: "sec-note" },
        "HP recorded after each floor. Thin lines are individual runs; a bold median appears"
        + " only when at least three runs are available. Select a line or result below to"
        + " open its turn-by-turn report."),
      chips,
      el("div", { class: "panel-card hp-chart-card" },
        el("div", { class: "hp-head" },
          el("div", { class: "who" }, configDot(cfg.color), el("b", {}, cfg.model)),
          el("span", { class: "hp-sub" },
            `${cfg.sub} · ${cfg.n} run${cfg.n === 1 ? "" : "s"}${cfg.wins ? ` · ${cfg.wins}W` : ""}`)),
        el("div", { class: "chart-scroll" }, hpChart(cfg)),
        legend),
    ];
  }, null, "health");
}

/* ---------- sortable tables ---------- */

function sortableHead(cols, sort) {
  return el("thead", {}, el("tr", {}, cols.map((col) => {
    const sorted = sort.key === col.key;
    return el("th", {
      class: [col.left ? "left" : "", col.sticky ? "sticky-col" : "", sorted ? "sorted" : "", col.key ? "" : "plain"].filter(Boolean).join(" "),
      title: col.title || null,
      "aria-sort": sorted ? (sort.desc ? "descending" : "ascending") : null,
    }, col.key ? el("button", {
      class: "sort-button",
      title: col.title || null,
      onclick: () => {
        if (sort.key === col.key) sort.desc = !sort.desc;
        else { sort.key = col.key; sort.desc = col.lowerBetter ? false : true; }
        rerender();
      },
    }, col.name, sorted ? (sort.desc ? " ↓" : " ↑") : "") : col.name);
  })));
}

function sortRows(rows, sort, valueOf) {
  return [...rows].sort((a, b) => {
    let av = valueOf(a, sort.key), bv = valueOf(b, sort.key);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "string" || typeof bv === "string") {
      return (sort.desc ? -1 : 1) * String(av).localeCompare(String(bv));
    }
    const c = av - bv;
    return sort.desc ? -c : c;
  });
}

/* ---------- strategy & cost ---------- */

const BEHAVIOR_COLS = [
  { key: "meanFloor", name: "Floor", title: "mean floor reached", cell: (r) => dec1(r.meanFloor) },
  { key: "winRate", name: "Wins", title: "victories out of attempted runs", cell: (r) => `${r.wins}/${r.n}` },
  { key: "skip", name: "Card skips", title: "card rewards declined out of card-reward decisions", cell: (r) => pct(r.skip) },
  { key: "potion", name: "Potions used", title: "potions used out of potions acquired", cell: (r) => pct(r.potion) },
  { key: "gold", name: "Gold spent", title: "gold spent out of starting gold plus gold earned", cell: (r) => pct(r.gold) },
];

const EFFICIENCY_COLS = [
  { key: "invalid", name: "Invalid decisions", title: "decisions with at least one rejected action", cell: (r) => pct(r.invalid) },
  { key: "lookups", name: "Lookups / decision", title: "observation lookups per decision",
    cell: (r) => (r.lookups == null ? "–" : r.lookups.toFixed(2)) },
  { key: "latency", name: "Median latency", title: "median model latency per decision", cell: (r) => secs(r.latency) },
  { key: "ptok", name: "Input tokens", title: "mean input tokens per run", cell: (r) => compact(r.ptok) },
  { key: "ctok", name: "Output tokens", title: "mean output tokens per run", cell: (r) => compact(r.ctok) },
  { key: "cost", name: "Cost / run", title: "mean estimated dollar cost per attempted run", lowerBetter: true, cell: (r) => money(r.cost) },
];

const STRAT_VIEWS = {
  behavior: {
    label: "Behavior",
    cols: BEHAVIOR_COLS,
    desc: "Performance and in-game resource choices. Rates state their denominator below the table.",
    note: "Card skips = rewards declined / card-reward decisions · Potions used = used / acquired · Gold spent = spent / starting gold plus earnings.",
  },
  efficiency: {
    label: "Reliability & cost",
    cols: EFFICIENCY_COLS,
    desc: "Action reliability, observation use, response time, token volume and estimated run cost.",
    note: "Invalid decisions = decisions with at least one rejected action / all decisions · Latency is the median model response time per decision.",
  },
};

function strategySection() {
  return section(() => {
    const sort = store.stratSort;
    const view = STRAT_VIEWS[store.stratView];
    const value = (r, key) => r[key];
    const llmRows = sortRows(LLM.filter((r) => ascMatch(r.ascension)), sort, value);
    const baselineRows = sortRows(BASELINES.filter((r) => ascMatch(r.ascension)), sort, value);
    const row = (r) => el("tr", { class: r.baseline ? "baseline-row" : null },
      el("td", { class: "left sticky-col" }, configDot(r.color), " ", r.model,
        el("span", { class: "sub" }, r.sub), " ",
        el("span", { class: "sample-badge" + (r.n === 1 ? " single" : "") }, `n=${r.n}`)),
      view.cols.map((col) => el("td", {}, col.cell(r))));
    return [
      sectionHead("Behavior, reliability & cost", view.desc,
        ctlCol("View", segGroup(Object.entries(STRAT_VIEWS).map(([key, item]) => ({
          label: item.label, on: store.stratView === key,
          onclick: () => {
            store.stratView = key;
            store.stratSort = key === "behavior"
              ? { key: "meanFloor", desc: true }
              : { key: "cost", desc: false };
            rerender();
          },
        })), "Metric group"))),
      el("div", { class: "panel-card table-card" }, el("table", { class: "data" },
        sortableHead([{ name: "Configuration", left: true, sticky: true }, ...view.cols], sort),
        el("tbody", {}, llmRows.map(row),
          baselineRows.length ? el("tr", { class: "table-band" },
            el("th", { colspan: view.cols.length + 1 }, "Baselines · calibration only")) : null,
          baselineRows.map(row)))),
      el("p", { class: "metric-defs" }, view.note),
    ];
  }, null, "behavior");
}

/* ---------- run log ---------- */

const outcomeClass = (o) => "outcome " + o.toLowerCase();
const runLabel = (id) => {
  const hit = String(id).match(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/);
  return hit ? `${hit[1]}-${hit[2]}-${hit[3]} ${hit[4]}:${hit[5]}` : id;
};

const RUN_COLS = [
  { key: "run_id", name: "Run", left: true, sticky: true },
  { key: "config", name: "Configuration", left: true },
  { key: "seed", name: "Seed", left: true },
  ...(MULTI_ASC ? [{ key: "asc", name: "Asc", left: true }] : []),
  { key: "outcome", name: "Outcome", left: true },
  { key: "floor", name: "Floor" },
  { key: "score", name: "Score", title: "Slay the Spire's end-of-run game score" },
  { key: "decisions", name: "Decisions" },
  { key: "invalid", name: "Invalid actions", title: "raw count of rejected model actions" },
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
        `${count}. Open a dated run for its full turn-by-turn report. Unfinished runs count`
        + " toward floor averages but never as wins; missing values remain unknown rather than zero."),
      el("div", { class: "panel-card table-card" }, el("table", { class: "data" },
        sortableHead(RUN_COLS, sort),
        el("tbody", {}, rows.map((run) => {
          const asc = ascOf(run);
          return el("tr", {},
            el("td", { class: "left mono-dim sticky-col" },
              run.page ? el("a", { href: run.page, target: "_blank", rel: "noreferrer" }, runLabel(run.run_id)) : runLabel(run.run_id)),
            el("td", { class: "left" },
              configDot(FAMILY_OF_LABEL.get(run.config) || "var(--dim)"), " ", run.config,
              ROW_BY_KEY.get(runExactKey(run))?.revision
                ? el("span", { class: "sub" }, ` revision ${ROW_BY_KEY.get(runExactKey(run)).revision}`)
                : null),
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
  }, null, "runs");
}

/* ---------- methodology ---------- */

function methodology() {
  return section(() => {
    const open = store.methodOpen;
    const toggle = el("button", {
      class: "method-toggle",
      "aria-expanded": open ? "true" : "false",
      "aria-controls": "method-panel",
      onclick: () => { store.methodOpen = !store.methodOpen; rerender(); },
    },
    el("span", { class: "arrow" + (open ? " open" : "") }, "▸"),
    "Methodology & caveats",
    EXCLUDED_RUNS.length
      ? el("span", { class: "badge" }, `· ${plural(EXCLUDED_RUNS.length, "pilot run")} excluded`)
      : DATA.warnings.length
        ? el("span", { class: "badge" }, `· ${DATA.warnings.length} provenance ${DATA.warnings.length === 1 ? "note" : "notes"}`)
      : null);

    const panel = open ? el("div", { class: "method-panel", id: "method-panel" },
      el("p", {},
        `Wins require clearing the Act III boss (floor ${WIN_FLOOR}); Act IV and the keys are`
        + " out of scope. The two baselines calibrate the scale: ",
        el("b", {}, "random"), " picks uniformly among validator-accepted actions, ",
        el("b", {}, "scripted"), " plays the first playable card. Beating scripted is evidence"
        + " of nontrivial decision-making, though it does not by itself establish robust"
        + " strategy. Single-run configurations are early signal — treat their numbers as"
        + " observations, not stable estimates."),
      el("p", {},
        "Unfinished and crashed runs contribute their observed floor to floor summaries but"
        + " never count as victories. Missing prices and game scores remain unknown rather"
        + " than being converted to zero."),
      EXCLUDED_RUNS.length ? [
        el("p", { class: "subhead" }, "Excluded pilot runs"),
        el("p", {},
          `${plural(EXCLUDED_RUNS.length, "early GPT-5.4 Mini pilot run")} used preliminary prompt drafts and `
          + `${EXCLUDED_RUNS.length === 1 ? "is" : "are"} excluded from every aggregate and chart on this page. `
          + "Their logs are retained for the technical write-up on how the benchmark evolved."),
      ] : null,
      DATA.warnings.length ? [
        el("p", { class: "subhead" }, "Prompt revisions"),
        el("p", {},
          "Some early runs predate the final wording of the prompt — revisions made while"
          + " the scaffold was being refined, not changes to the game or the rules. Runs"
          + " are grouped by the exact version they saw, so no number averages across"
          + " revisions:"),
        DATA.warnings.map((w) => el("div", { class: "provenance" }, w)),
      ] : null) : null;

    return [toggle, panel];
  }, "method", "methodology");
}

function pageFooter() {
  const generated = new Date(DATA.generated_at).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
  return el("footer", {},
    el("div", { class: "footer-links" },
      el("a", { href: "https://github.com/alavico/sts-bench", target: "_blank", rel: "noreferrer" }, "Source code"),
      el("a", { href: "#methodology" }, "Methodology"),
      el("a", { href: "https://www.megacrit.com/", target: "_blank", rel: "noreferrer" }, "Mega Crit")),
    el("div", {}, `Last generated ${generated} · ${plural(DATA.runs.length, "run")}`),
    el("div", { class: "footer-note" },
      "STS-Bench is an independent research project and is not affiliated with Mega Crit."));
}

/* ---------- page ---------- */

document.getElementById("app").append(
  themeToggle(),
  el("main", {},
    header(),
    contextBar(),
    leaderboard(),
    seedGrid(),
    costSection(),
    hpSection(),
    strategySection(),
    runLog(),
    methodology(),
    pageFooter()));
