"use strict";
/* Pure renderer: all data shaping happened before embedding. The page reads
   the JSON payload and builds the DOM; nothing here re-derives game facts. */

const DATA = JSON.parse(document.getElementById("data").textContent);

/* ---------- theme ---------- */

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
const num = (v) => (v == null ? "?" : fmt.format(v));
const signed = (v) => (v > 0 ? "+" + num(v) : num(v));
const title = (s) => (s ? s[0].toUpperCase() + s.slice(1).toLowerCase() : s);

/* ---------- room styling ---------- */

const ROOM = {
  MonsterRoom: ["Monster", "#e06c5b", "M"],
  MonsterRoomElite: ["Elite", "#b07cf0", "E"],
  MonsterRoomBoss: ["Boss", "#ff5757", "B"],
  EventRoom: ["Event", "#5bc0de", "?"],
  NeowRoom: ["Start", "#9aa0a6", "N"],
  ShopRoom: ["Shop", "#e8c860", "$"],
  RestRoom: ["Rest", "#7ed87e", "R"],
  TreasureRoom: ["Chest", "#f0a850", "T"],
  TreasureRoomBoss: ["Boss chest", "#f0a850", "T"],
};

function room(type) {
  const entry = ROOM[type];
  if (entry) return { label: entry[0], color: entry[1], symbol: entry[2] };
  return { label: type || "?", color: "#9aa0a6", symbol: "?" };
}

const SYMBOL_COLOR = {};
for (const [, color, symbol] of Object.values(ROOM)) {
  if (!(symbol in SYMBOL_COLOR)) SYMBOL_COLOR[symbol] = color;
}

const FLOOR_BY_NO = new Map(DATA.floors.map((f) => [f.floor, f]));

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

const tipLine = (text) => el("div", {}, text);

/* The floor tooltip is a small card: room header, aligned stat rows with
   colored deltas, then anything gained. */
function tipCard(floor, exit = false) {
  const info = room(floor.type);
  const sc = floor.scorecard;
  const rows = [];
  const row = (k, ...v) => rows.push(el("span", { class: "k" }, k), el("span", {}, ...v));
  const delta = (v) => el("b", { class: v > 0 ? "up" : v < 0 ? "down" : "flat" }, ` ${signed(v)}`);

  row("HP", `${floor.entry.hp} → ${floor.exit.hp}`, delta(sc.hp_delta));
  row("Gold", `${floor.entry.gold} → ${floor.exit.gold}`, delta(sc.gold_delta));
  if (sc.combat_turns) row("Combat", `${sc.combat_turns} turns`);
  row("Decisions", String(floor.decisions.length));
  row("Tokens", num(floor.usage.prompt + floor.usage.completion));
  if (floor.reward) row("Reward", floor.reward.total > 0 ? "+" + floor.reward.total : String(floor.reward.total));

  const gains = [...sc.cards_gained, ...sc.relics_gained, ...sc.potions_gained];
  return el("div", { class: "tipcard" },
    el("div", { class: "tip-head" },
      el("span", { class: "sym", style: "background:" + info.color }, info.symbol),
      el("b", {}, exit ? "End of run" : "Floor " + floor.floor),
      el("span", { class: "dim" }, exit ? ` · after floor ${floor.floor}` : ` · ${info.label}`)),
    el("div", { class: "tip-rows" }, rows),
    gains.length ? el("div", { class: "tip-gains" }, gains.map((g) => el("span", { class: "chip" }, g))) : null);
}

/* ---------- linked floors ----------
   Every chart mark, bar, axis symbol, and route node that stands for a floor
   registers here; hovering any of them lights up all of its siblings. */

const FLOOR_LINKS = new Map();

function highlightFloor(floorNo, on) {
  for (const elm of FLOOR_LINKS.get(floorNo) || []) elm.classList.toggle("hl", on);
}

function linkFloor(elm, floorNo, buildTip) {
  let links = FLOOR_LINKS.get(floorNo);
  if (!links) FLOOR_LINKS.set(floorNo, links = []);
  links.push(elm);
  elm.addEventListener("mouseenter", (e) => {
    highlightFloor(floorNo, true);
    showTipNode(e, buildTip());
  });
  elm.addEventListener("mouseleave", () => {
    highlightFloor(floorNo, false);
    hideTip();
  });
  elm.addEventListener("mousemove", moveTip);
  elm.addEventListener("click", () => openFloor(floorNo));
  return elm;
}

/* ---------- floor detail modal ----------
   Charts and maps open a floor in a modal so the reader never loses their
   place; the full floor-by-floor log stays at the bottom of the page. */

let modalEl = null;

function closeModal() {
  if (modalEl) {
    modalEl.remove();
    modalEl = null;
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

function openFloor(floorNo) {
  const floor = FLOOR_BY_NO.get(floorNo);
  if (!floor) return;
  hideTip();
  closeModal();
  const idx = DATA.floors.indexOf(floor);
  const nav = (delta, glyph) => {
    const target = DATA.floors[idx + delta];
    return el("button", {
      class: "modal-nav",
      disabled: target ? null : "",
      onclick: target ? () => openFloor(target.floor) : null,
    }, glyph);
  };
  modalEl = el("div", {
    class: "modal-backdrop",
    onclick: (e) => { if (e.target === modalEl) closeModal(); },
  },
    el("div", { class: "modal", role: "dialog", "aria-label": "Floor " + floorNo },
      el("div", { class: "modal-head" },
        nav(-1, "‹"), nav(1, "›"),
        floorHeading(floor),
        el("span", { class: "spacer" }),
        el("button", {
          class: "modal-link",
          onclick: () => { closeModal(); revealFloor(floorNo); },
        }, "Full log ↓"),
        el("button", { class: "modal-close", onclick: closeModal, "aria-label": "Close" }, "×")),
      el("div", { class: "modal-body" },
        el("div", { class: "meta" }, floorMeta(floor)),
        floorBody(floor))));
  document.body.append(modalEl);
}

function revealFloor(floorNo) {
  const target = document.getElementById("floor-" + floorNo);
  if (!target) return;
  target.open = true;
  target.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------- header ---------- */

function sumFloorUsage() {
  const total = { prompt: 0, completion: 0, reasoning: 0, cached: 0 };
  for (const floor of DATA.floors) {
    total.prompt += floor.usage.prompt;
    total.completion += floor.usage.completion;
    total.reasoning += floor.usage.reasoning;
    total.cached += floor.usage.cached;
  }
  return total;
}

function header() {
  const run = DATA.run;
  const where = run.floor_reached != null ? ` on floor ${run.floor_reached}` : "";
  const config = [
    run.model,
    run.agent && run.agent + " agent",
    run.character && `${title(run.character)} · ascension ${run.ascension}`,
    "seed " + (run.seed || "random"),
    run.api && run.api + " API" + (run.reasoning_effort ? `, effort ${run.reasoning_effort}` : ""),
    run.started_at && new Date(run.started_at).toLocaleString(),
    run.run_id,
  ].filter(Boolean).join("  ·  ");

  const usage = run.usage || sumFloorUsage();
  const latency = DATA.floors.reduce((sum, floor) => sum + (floor.latency_ms || 0), 0);
  const modelTime = latency >= 90000
    ? Math.round(latency / 60000) + " min"
    : Math.round(latency / 1000) + " s";
  const decisions = run.decisions ?? DATA.floors.reduce((sum, floor) => sum + floor.decisions.length, 0);

  const head = el("header", {},
    el("h1", {}, "STS-Bench Run Report"),
    el("div", { class: "runline" },
      el("span", { class: "verdict " + run.verdict.toLowerCase() }, title(run.verdict) + where),
      run.score != null ? chip("score", num(run.score)) : null),
    el("div", { class: "configline" }, config),
    el("div", { class: "stats" },
      chip("floors", num(DATA.floors.length)),
      chip("decisions", num(decisions)),
      run.forced ? chip("forced", num(run.forced)) : null,
      chip("prompt tokens", num(usage.prompt)),
      chip("completion tokens", num(usage.completion)),
      usage.reasoning ? chip("reasoning tokens", num(usage.reasoning)) : null,
      chip("model time", modelTime)));

  if (run.missing) {
    head.append(el("div", { class: "warn" },
      "No run record: the run died mid-flight; the floors below are what completed"));
  }
  if (run.verdict === "DEFEAT" && DATA.floors.length) {
    const last = DATA.floors[DATA.floors.length - 1];
    head.append(el("div", { class: "death-link" },
      el("a", {
        href: "#floor-" + last.floor,
        onclick: (e) => { e.preventDefault(); openFloor(last.floor); },
      }, `Post-mortem: floor ${last.floor} — ${room(last.type).label}`)));
  }
  return head;
}

function chip(label, value) {
  return el("span", { class: "chip" }, el("b", {}, value), label ? " " + label : "");
}

/* ---------- overview: charts beside the route ---------- */

function section(name, ...body) {
  return el("section", {}, el("h2", {}, name), ...body);
}

function overview() {
  const floors = DATA.floors;
  if (!floors.length) return section("Run at a glance", el("p", { class: "dim" }, "No floors recorded"));
  return section("Run at a glance",
    el("div", { class: "glance" },
      el("div", { class: "glance-charts" },
        hpChart(floors),
        toggledStrip(floors),
        legendRow()),
      actMaps()));
}

/* One slot for the secondary metrics: gold is a state and renders as a line,
   tokens and decisions are per-floor amounts and render as bars. All share
   the HP chart's x scale, so the columns stay floor-aligned. */
function toggledStrip(floors) {
  const metrics = [
    ["Gold", () => goldChart(floors)],
    ["Tokens", () => strip(floors, "Tokens per floor",
      (f) => f.usage.prompt + f.usage.completion, "tokens", { unit: "tokens" })],
    ["Decisions", () => strip(floors, "Decisions per floor",
      (f) => f.decisions.length, "decisions", {
        unit: "decisions",
        note: "red: rejected attempts",
        overlay: (f) => f.decisions.reduce((sum, d) => sum + d.invalid, 0),
      })],
  ];
  const slot = el("div", {});
  const pills = metrics.map(([name, render], i) =>
    el("button", {
      class: "pill" + (i === 0 ? " on" : ""),
      onclick: () => {
        pills.forEach((p, j) => p.classList.toggle("on", j === i));
        slot.replaceChildren(render());
      },
    }, name));
  slot.replaceChildren(metrics[0][1]());
  return el("div", {}, el("div", { class: "pills" }, pills), slot);
}

function legendRow() {
  const seen = new Set();
  const items = [];
  for (const [label, color, symbol] of Object.values(ROOM)) {
    if (seen.has(symbol)) continue;
    seen.add(symbol);
    items.push(el("span", {},
      el("span", { class: "dot", style: "background:" + color }), `${symbol} ${label}`));
  }
  return el("div", { class: "legend-row" }, items);
}

/* Shared horizontal scale: the line charts plot floors.length+1 points (every
   room entry plus the final exit), and the bar strips center their bars on
   the same x positions so the columns line up. */
const CHART_W = 940, CHART_L = 46, CHART_R = 16;

function chartX(index, count) {
  return CHART_L + (index * (CHART_W - CHART_L - CHART_R)) / Math.max(count - 1, 1);
}

const axisNum = (v) => (v >= 10000 ? Math.round(v / 1000) + "k" : num(v));

/* Quarter gridlines with value ticks plus a rotated unit label on the left.
   Small ranges round to duplicate ticks, so only distinct values are drawn. */
function yGrid(top, y, label, yTop, yBottom) {
  const seen = new Set();
  const marks = [];
  for (const frac of [0, 0.25, 0.5, 0.75, 1]) {
    const v = Math.round(top * frac);
    if (seen.has(v)) continue;
    seen.add(v);
    marks.push(
      sv("line", { x1: CHART_L, x2: CHART_W - CHART_R, y1: y(v), y2: y(v), class: "grid" }),
      sv("text", { x: CHART_L - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" }, axisNum(v)));
  }
  const mid = (yTop + yBottom) / 2;
  marks.push(sv("text", {
    x: 14, y: mid, class: "axis-label", "text-anchor": "middle",
    transform: `rotate(-90 14 ${mid})`,
  }, label));
  return marks;
}

function floorAxis(floors, x, H) {
  const every = floors.length > 35 ? 5 : 1;
  return [
    floors.map((f, i) => f.floor % every === 0
      ? sv("text", { x: x(i), y: H - 22, class: "axis", "text-anchor": "middle" }, f.floor)
      : null),
    sv("text", {
      x: (CHART_L + CHART_W - CHART_R) / 2, y: H - 6,
      class: "axis-label", "text-anchor": "middle",
    }, "Floor"),
  ];
}

/* Faint vertical rules where the run crosses into a new act. */
function actSeparators(floors, x, yTop, yBottom) {
  const marks = [];
  for (let i = 1; i < floors.length; i++) {
    if (floors[i].act == null || floors[i].act === floors[i - 1].act) continue;
    const xm = (x(i - 1) + x(i)) / 2;
    marks.push(
      sv("line", { x1: xm, x2: xm, y1: yTop, y2: yBottom, class: "act-sep" }),
      sv("text", { x: xm + 5, y: yTop + 10, class: "act-label" }, "Act " + floors[i].act));
  }
  if (marks.length && floors[0].act != null) {
    marks.push(sv("text", { x: CHART_L + 4, y: yTop + 10, class: "act-label" }, "Act " + floors[0].act));
  }
  return marks;
}

function hpChart(floors) {
  const H = 320, T = 30, B = 58;
  const pts = floors.map((f) => ({ hp: f.entry.hp ?? 0, max: f.entry.max_hp ?? 0, f, exit: false }));
  const last = floors[floors.length - 1];
  pts.push({ hp: last.exit.hp ?? 0, max: last.exit.max_hp ?? 0, f: last, exit: true });
  const top = Math.max(...pts.map((p) => p.max), 1);
  const x = (i) => chartX(i, pts.length);
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const hpPath = pts.map((p, i) => (i ? "L" : "M") + x(i) + " " + y(p.hp)).join("");
  const maxPath = pts.map((p, i) => (i ? "L" : "M") + x(i) + " " + y(p.max)).join("");
  const numberEvery = floors.length > 35 ? 5 : 1;

  return sv("svg", { viewBox: `0 0 ${CHART_W} ${H}`, class: "chart" },
    yGrid(top, y, "HP", T, H - B),
    actSeparators(floors, x, T, H - B),
    sv("text", { x: CHART_L, y: 16, class: "chart-title" }, "HP at room entry"),
    sv("text", { x: CHART_W - CHART_R, y: 16, class: "legend", "text-anchor": "end" },
      "dotted: max HP · last point: end of run · hover a floor, click for details"),
    sv("path", { d: maxPath, class: "maxhp" }),
    sv("path", { d: hpPath, class: "hp" }),
    pts.map((p, i) => {
      if (p.exit) return null;
      const info = room(p.f.type);
      return [
        p.f.floor % numberEvery === 0
          ? sv("text", { x: x(i), y: H - 38, class: "axis", "text-anchor": "middle" }, p.f.floor)
          : null,
        linkFloor(sv("text", {
          x: x(i), y: H - 22, class: "sym-axis", fill: info.color,
        }, info.symbol), p.f.floor, () => tipCard(p.f)),
      ];
    }),
    sv("text", { x: (CHART_L + CHART_W - CHART_R) / 2, y: H - 6, class: "axis-label", "text-anchor": "middle" }, "Floor"),
    pts.map((p, i) => marker(p, x(i), y(p.hp))));
}

function marker(point, cx, cy) {
  const floor = point.f;
  const dot = sv("circle", {
    cx, cy,
    r: point.exit ? 4 : 5,
    class: "mark" + (point.exit ? " exit" : ""),
  });
  if (point.exit) {
    dot.addEventListener("mouseenter", (e) => showTipNode(e, tipCard(floor, true)));
    dot.addEventListener("mouseleave", hideTip);
    dot.addEventListener("mousemove", moveTip);
    dot.addEventListener("click", () => openFloor(floor.floor));
    return dot;
  }
  dot.style.setProperty("--room", room(floor.type).color);
  return linkFloor(dot, floor.floor, () => tipCard(floor));
}

/* Gold is a running state like HP, so it gets the same treatment: a line
   sampled at every room entry, closing with the end-of-run value. */
function goldChart(floors) {
  const H = 210, T = 26, B = 38;
  const pts = floors.map((f) => ({ gold: f.entry.gold ?? 0, f }));
  const last = floors[floors.length - 1];
  pts.push({ gold: last.exit.gold ?? 0, f: last, exit: true });
  const top = Math.max(...pts.map((p) => p.gold), 1);
  const x = (i) => chartX(i, pts.length);
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const path = pts.map((p, i) => (i ? "L" : "M") + x(i) + " " + y(p.gold)).join("");

  return sv("svg", { viewBox: `0 0 ${CHART_W} ${H}`, class: "chart strip" },
    yGrid(top, y, "Gold", T, H - B),
    actSeparators(floors, x, T, H - B),
    floorAxis(floors, x, H),
    sv("text", { x: CHART_L, y: 16, class: "chart-title" }, "Gold at room entry"),
    sv("path", { d: path, class: "goldline" }),
    pts.map((p, i) => {
      const dot = sv("circle", { cx: x(i), cy: y(p.gold), r: 3, class: "mark-gold" });
      if (p.exit) {
        dot.addEventListener("mouseenter", (e) => showTipNode(e, tipLine(`End of run: ${num(p.gold)} gold`)));
        dot.addEventListener("mouseleave", hideTip);
        dot.addEventListener("mousemove", moveTip);
        return dot;
      }
      return linkFloor(dot, p.f.floor, () => tipLine(`Floor ${p.f.floor}: ${num(p.gold)} gold`));
    }));
}

/* Per-floor amounts (spend, decision counts) stay as bars: each floor is a
   discrete unit of work, and bars compare those units without implying a
   trend between them. */
function strip(floors, name, value, cls, opts = {}) {
  const H = 210, T = 26, B = 38;
  const values = floors.map(value);
  const top = Math.max(...values, 1);
  const count = floors.length + 1; // align bars with the line charts' points
  const x = (i) => chartX(i, count);
  const step = (CHART_W - CHART_L - CHART_R) / Math.max(count - 1, 1);
  const barWidth = Math.max(4, step * 0.55);
  const y = (v) => T + (1 - v / top) * (H - T - B);

  const bars = floors.map((floor, i) => {
    const cx = chartX(i, count);
    const over = opts.overlay ? opts.overlay(floor) : 0;
    const tipText = `Floor ${floor.floor}: ${num(values[i])} ${opts.unit || ""}`.trim()
      + (over ? ` (${num(over)} rejected)` : "");
    const bar = sv("rect", {
      x: cx - barWidth / 2, y: y(values[i]),
      width: barWidth, height: Math.max(0, H - B - y(values[i])),
      class: "bar " + cls,
    });
    const items = [linkFloor(bar, floor.floor, () => tipLine(tipText))];
    if (over) {
      items.push(sv("rect", {
        x: cx - barWidth / 2, y: y(over),
        width: barWidth, height: Math.max(0, H - B - y(over)),
        class: "bar rejected",
      }));
    }
    if (opts.overlay && floor.decisions.some((d) => d.forced)) {
      items.push(sv("text", { x: cx, y: 13, "text-anchor": "middle", class: "warn-mark" }, "⚠"));
    }
    return items;
  });

  return sv("svg", { viewBox: `0 0 ${CHART_W} ${H}`, class: "chart strip" },
    yGrid(top, y, title(opts.unit || ""), T, H - B),
    actSeparators(floors, x, T, H - B),
    floorAxis(floors, x, H),
    sv("text", { x: CHART_L, y: 16, class: "chart-title" }, name,
      opts.note ? sv("tspan", { class: "legend" }, "  —  " + opts.note) : null),
    bars);
}

/* ---------- act maps ---------- */

/* All acts share one panel; pills swap which act's map is shown. Each act's
   svg is built exactly once so its nodes register their floor links once,
   then the pills only move the prebuilt svg in and out of the slot. */
function actMaps() {
  const acts = DATA.acts;
  if (!acts.length) return null;
  const bodies = acts.map(actMapBody);
  const slot = el("div", {});
  const show = (i) => {
    pills.forEach((p, j) => p.classList.toggle("on", j === i));
    slot.replaceChildren(bodies[i]);
  };
  const pills = acts.map((act, i) =>
    el("button", { class: "pill", onclick: () => show(i) }, "Act " + act.act));
  show(acts.length - 1); // open on the act the run ended in
  return el("div", { class: "map" },
    acts.length > 1 ? el("div", { class: "pills" }, pills) : el("h3", {}, "Act " + acts[0].act),
    slot);
}

function actMapBody(act) {
  if (!act.nodes.length) {
    return el("div", { class: "map-missing" }, `Act ${act.act}: no map captured`);
  }
  const cols = Math.max(...act.nodes.map((n) => n.x)) + 1;
  const rows = Math.max(...act.nodes.map((n) => n.y)) + 1;
  const cw = 40, ch = 28, pad = 18, top = 56;
  const W = pad * 2 + cols * cw;
  const H = pad * 2 + rows * ch + top;
  const px = (v) => pad + v * cw + cw / 2;
  const py = (v) => H - pad - v * ch - ch / 2;

  const visited = new Map(act.path.filter((p) => !p.boss).map((p) => [p.x + "," + p.y, p]));
  const bossStep = act.path.find((p) => p.boss);

  // The map's own edges name where the boss sits: every top-row node points
  // at it. Anchoring the boss there keeps the route flush with those edges.
  const bossEdge = act.edges.find(([, , , childY]) => childY >= rows);
  const boss = bossEdge
    ? { x: px(bossEdge[2]), y: py(bossEdge[3]) }
    : { x: W / 2, y: pad + 14 };

  const edges = act.edges.map(([x1, y1, x2, y2]) =>
    sv("line", { x1: px(x1), y1: py(y1), x2: px(x2), y2: py(y2), class: "map-edge" }));

  const routePts = act.path.filter((p) => !p.boss).map((p) => px(p.x) + "," + py(p.y));
  if (bossStep && routePts.length) routePts.push(boss.x + "," + boss.y);
  const route = routePts.length > 1
    ? sv("polyline", { points: routePts.join(" "), class: "route" })
    : null;

  const mapNode = (cx, cy, symbol, color, step) => {
    const node = sv("g", { class: "map-node" + (step ? " visited" : "") },
      sv("circle", { cx, cy, r: step ? 10 : 7, fill: step ? color : null, stroke: color }),
      sv("text", {
        x: cx, y: cy + 3.5, "text-anchor": "middle",
        class: "map-symbol" + (step ? " on" : ""),
      }, symbol));
    if (step) linkFloor(node, step.floor, () => floorTipFor(step.floor));
    return node;
  };

  const nodes = act.nodes.map((node) =>
    mapNode(px(node.x), py(node.y), node.symbol || "?",
      SYMBOL_COLOR[node.symbol] || "#9aa0a6",
      visited.get(node.x + "," + node.y)));

  const bossNode = [
    mapNode(boss.x, boss.y, "B", SYMBOL_COLOR.B, bossStep),
    sv("text", {
      x: boss.x, y: boss.y - 16, "text-anchor": "middle",
      class: "boss-name" + (bossStep ? " taken" : ""),
    }, act.boss || "?"),
  ];

  return sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "act-map", style: `width:${W}px` },
    edges, route, nodes, bossNode);
}

function floorTipFor(floorNo) {
  const floor = FLOOR_BY_NO.get(floorNo);
  return floor ? tipCard(floor) : tipLine(`Floor ${floorNo}`);
}

/* ---------- deck and collection ---------- */

const CARD_COST = (cost) =>
  cost == null ? "·" : cost === -1 ? "X" : cost < 0 ? "·" : String(cost);

function cardChip(card) {
  const chipEl = el("span", { class: "card-chip t-" + (card.type || "NONE") },
    el("b", { class: "cost" }, CARD_COST(card.cost)),
    card.name,
    card.count > 1 ? el("span", { class: "cnt" }, "×" + card.count) : null);
  if (card.text) {
    chipEl.addEventListener("mouseenter", (e) => showTipNode(e, el("div", { class: "tipcard" },
      el("div", { class: "tip-head" }, el("b", {}, card.name),
        el("span", { class: "dim" }, ` · ${CARD_COST(card.cost)} energy · ${title(card.type)}`)),
      el("div", {}, card.text))));
    chipEl.addEventListener("mouseleave", hideTip);
    chipEl.addEventListener("mousemove", moveTip);
  }
  return chipEl;
}

function itemChip(item) {
  const chipEl = el("span", { class: "item-chip" },
    item.name,
    el("span", { class: "cnt" }, item.floor == null ? "start" : "floor " + item.floor));
  if ("fate" in item) {
    if (item.fate) {
      const fate = el("span", { class: "cnt fate" }, `→ ${item.fate} floor ${item.fate_floor}`);
      if (FLOOR_BY_NO.has(item.fate_floor)) {
        fate.classList.add("linked");
        fate.addEventListener("click", (e) => { e.stopPropagation(); openFloor(item.fate_floor); });
      }
      chipEl.append(fate);
    } else {
      chipEl.append(el("span", { class: "cnt never" }, "→ never used"));
    }
  }
  if (item.text) {
    chipEl.addEventListener("mouseenter", (e) => showTipNode(e, el("div", { class: "tipcard" },
      el("div", { class: "tip-head" }, el("b", {}, item.name)),
      el("div", {}, item.text))));
    chipEl.addEventListener("mouseleave", hideTip);
    chipEl.addEventListener("mousemove", moveTip);
  }
  if (item.floor != null && FLOOR_BY_NO.has(item.floor)) {
    chipEl.classList.add("linked");
    chipEl.addEventListener("click", () => openFloor(item.floor));
  }
  return chipEl;
}

function deckView() {
  const deck = DATA.deck;
  if (!deck && !DATA.relics.length && !DATA.potions.length) return null;
  const cardCount = (deck || []).reduce((sum, c) => sum + c.count, 0);
  return section("Deck",
    el("div", { class: "collection" },
      deck ? panel(`Final deck — ${cardCount} cards`,
        el("div", { class: "cards" }, deck.map(cardChip)),
        el("div", { class: "panel-note" },
          "Edge color marks the card type:",
          [["var(--red)", "attack"], ["var(--green)", "skill"], ["var(--blue)", "power"],
            ["#b07cf0", "curse"], ["var(--dim)", "status"]].map(([color, type]) => [
            el("span", { class: "dot", style: "background:" + color }), type]))) : null,
      el("div", { class: "items" },
        DATA.relics.length ? panel("Relics",
          el("div", { class: "cards" }, DATA.relics.map(itemChip))) : null,
        DATA.potions.length ? panel("Potions found",
          el("div", { class: "cards" }, DATA.potions.map(itemChip))) : null)));
}

/* ---------- behavior panels ---------- */

const ACTION_TOOLS = new Set([
  "play_card", "end_turn", "choose", "use_potion", "discard_potion", "proceed", "return_back",
]);

function behaviorView() {
  const all = DATA.floors.flatMap((floor) => floor.decisions.map((d) => ({ floor, d })));

  const mix = new Map();
  for (const { d } of all) {
    const key = d.forced ? "forced" : (d.command || "?").split(" ")[0];
    mix.set(key, (mix.get(key) || 0) + 1);
  }

  let takes = 0, skips = 0;
  for (const { d } of all) {
    if (d.screen !== "CARD_REWARD") continue;
    if ((d.command || "").startsWith("choose")) takes++;
    else skips++;
  }

  const lookups = new Map();
  for (const { d } of all) {
    for (const ev of d.events) {
      if (ev.kind === "call" && !ACTION_TOOLS.has(ev.name)) {
        lookups.set(ev.name, (lookups.get(ev.name) || 0) + 1);
      }
    }
  }

  const rejections = [];
  for (const { floor, d } of all) {
    for (const ev of d.events) {
      if (ev.kind === "rejection") rejections.push({ floor: floor.floor, decision: d.index, text: ev.text });
    }
  }

  const violations = DATA.floors.flatMap((floor) =>
    floor.violations.map((v) => `floor ${floor.floor}: ${v}`));

  return section("Behavior",
    violations.length
      ? el("div", { class: "warn" }, "Packet property violated — ", violations.join(" | "))
      : null,
    el("div", { class: "panels" },
      panel("Actions", barList([...mix.entries()].sort((a, b) => b[1] - a[1]))),
      panel("Card rewards", el("div", { class: "big-stat" },
        el("div", {}, el("b", {}, takes), " taken"),
        el("div", {}, el("b", {}, skips), " skipped"))),
      potionPanel(),
      panel("Observation lookups", lookups.size
        ? barList([...lookups.entries()].sort((a, b) => b[1] - a[1]))
        : el("div", { class: "dim" }, "None — the model never used a lookup tool"))),
    rejections.length
      ? panel(`Rejected actions (${rejections.length})`,
        el("div", { class: "rejections" }, rejections.map((r) =>
          el("div", { class: "rej" },
            el("a", {
              href: "#floor-" + r.floor,
              onclick: (e) => { e.preventDefault(); openFloor(r.floor); },
            }, `floor ${r.floor} · #${r.decision}`),
            " — " + r.text))))
      : null);
}

function panel(name, ...body) {
  return el("div", { class: "panel" }, el("h3", {}, name), body);
}

/* Hoarding is a classic agent failure: a potion that was never drunk did
   nothing for the run, so the panel leads with used vs. never used. */
function potionPanel() {
  if (!DATA.potions.length) {
    return panel("Potions", el("div", { class: "dim" }, "None seen this run"));
  }
  const used = DATA.potions.filter((p) => p.fate === "used").length;
  const discarded = DATA.potions.filter((p) => p.fate === "discarded").length;
  const triggered = DATA.potions.filter((p) => p.fate === "triggered").length;
  const unused = DATA.potions.length - used - discarded - triggered;
  return panel("Potions", el("div", { class: "big-stat" },
    el("div", {}, el("b", {}, used), " used"),
    discarded ? el("div", {}, el("b", {}, discarded), " discarded") : null,
    triggered ? el("div", {}, el("b", {}, triggered), " triggered") : null,
    el("div", {}, el("b", {}, unused), " never used")));
}

function barList(entries) {
  const top = Math.max(...entries.map((e) => e[1]), 1);
  return el("div", { class: "barlist" }, entries.map(([key, value]) =>
    el("div", { class: "barrow" },
      el("span", { class: "bk" }, key),
      el("span", { class: "bv", style: `width:${Math.max(2, Math.round((value / top) * 140))}px` }),
      el("span", { class: "bn" }, num(value)))));
}

/* ---------- floors ---------- */

function floorsView() {
  return section("Floors",
    DATA.system_prompt
      ? el("details", { class: "sysprompt" },
        el("summary", {}, "System prompt (shown once)"),
        el("pre", {}, DATA.system_prompt))
      : null,
    DATA.floors.map(floorView),
    DATA.orphans.map((o) => el("div", { class: "warn" },
      `Floor ${o.floor}: ${o.decisions} decisions but no floor record (run died mid-floor)`)));
}

function floorHeading(floor) {
  const info = room(floor.type);
  return el("span", { class: "floor-heading" },
    el("span", { class: "sym", style: "background:" + info.color }, info.symbol),
    el("b", {}, `Floor ${floor.floor}`), `· ${info.label}`);
}

function floorMeta(floor) {
  const sc = floor.scorecard;
  return ` HP ${floor.entry.hp}→${floor.exit.hp} (${signed(sc.hp_delta)}) · gold ${floor.entry.gold}→${floor.exit.gold}` +
    (sc.combat_turns ? ` · ${sc.combat_turns} turns` : "") +
    ` · ${floor.decisions.length} decisions · ${num(floor.usage.prompt + floor.usage.completion)} tok` +
    (floor.reward ? ` · reward ${floor.reward.total > 0 ? "+" : ""}${floor.reward.total}` : "");
}

function floorBody(floor) {
  const sc = floor.scorecard;
  const body = el("div", { class: "floor-body" });
  if (floor.violations.length) {
    body.append(el("div", { class: "warn" }, "Packet violations: " + floor.violations.join(" | ")));
  }
  const gains = [
    ...sc.cards_gained.map((g) => ["card", g]),
    ...sc.relics_gained.map((g) => ["relic", g]),
    ...sc.potions_gained.map((g) => ["potion", g]),
  ];
  const changes = floor.deck_changes;
  if (gains.length || changes) {
    body.append(el("div", { class: "gains" },
      gains.map(([kind, name]) => gainChip(kind, "+", name, title(kind) + " gained")),
      changes ? changes.removed.map((name) => gainChip("card", "−", name, "Card removed from the deck")) : null,
      changes ? changes.upgraded.map((name) => gainChip("card", "↑", name, "Card upgraded")) : null));
  }
  if (floor.reward && Object.keys(floor.reward.components || {}).length) {
    body.append(el("div", { class: "dim small" },
      `reward ${floor.reward.spec}: ` +
      Object.entries(floor.reward.components)
        .map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`).join(", ")));
  }
  if (floor.turns) body.append(fightsView(floor.turns));
  body.append(el("div", { class: "decisions" }, floor.decisions.map(decisionView)));
  return body;
}

/* Gain chips follow one color rule: the hue names what the thing is (card
   blue, relic gold, potion green) and the glyph names what happened to it
   (+ gained, − removed, ↑ upgraded). Hovering spells it out in words. */
function gainChip(kind, glyph, name, what) {
  const chipEl = el("span", { class: "chip gain " + kind }, glyph + " " + name);
  chipEl.addEventListener("mouseenter", (e) => showTipNode(e, tipLine(what)));
  chipEl.addEventListener("mouseleave", hideTip);
  chipEl.addEventListener("mousemove", moveTip);
  return chipEl;
}

function floorView(floor) {
  const details = el("details", { class: "floor", id: "floor-" + floor.floor });
  details.append(el("summary", {},
    floorHeading(floor),
    el("span", { class: "meta" }, floorMeta(floor))));
  details.append(floorBody(floor));
  return details;
}

function fightsView(turns) {
  // A turn number that fails to climb starts a new fight on the same floor.
  const fights = [];
  let lastTurn = Infinity;
  for (const turn of turns) {
    if (turn.turn <= lastTurn) fights.push([]);
    fights[fights.length - 1].push(turn);
    lastTurn = turn.turn;
  }
  return el("div", { class: "fights" },
    el("div", { class: "spark-legend" },
      "Fight sparkline — HP turn by turn:",
      el("span", { class: "dot", style: "background: var(--green)" }), "player",
      el("span", { class: "dot", style: "background: var(--red)" }), "all enemies combined"),
    fights.map((fight) =>
      el("div", { class: "fight" },
        el("div", { class: "fight-summary" }, fightSummary(fight)),
        fightSpark(fight),
        turnTable(fight))));
}

function fightSummary(turns) {
  const names = [...new Set(turns.flatMap((t) => t.enemies.map((e) => e.name)))];
  const first = turns[0], last = turns[turns.length - 1];
  const potions = turns.reduce((sum, t) =>
    sum + t.actions.filter((a) => a.text.startsWith("use_potion")).length, 0);
  const parts = [
    names.join(", "),
    `${turns.length} ${turns.length === 1 ? "turn" : "turns"}`,
    `HP ${first.hp ?? "?"} → ${last.hp ?? "?"}`,
  ];
  if (potions) parts.push(`${potions} ${potions === 1 ? "potion" : "potions"} used`);
  return parts.join("  ·  ");
}

function fightSpark(turns) {
  const W = 36 + turns.length * 26, H = 56, L = 6, T = 8, B = 8;
  const enemyTotal = (t) => t.enemies.reduce((sum, e) => sum + e.hp, 0);
  const enemyMax = Math.max(...turns.map((t) => t.enemies.reduce((sum, e) => sum + e.max_hp, 0)), 1);
  const top = Math.max(...turns.map((t) => t.max_hp || 0), enemyMax, 1);
  const x = (i) => L + 12 + i * 26;
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const line = (values, cls) =>
    sv("polyline", { points: values.map((v, i) => x(i) + "," + y(v)).join(" "), class: cls });
  return sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark", style: `width:${W}px` },
    line(turns.map((t) => t.hp ?? 0), "spark-you"),
    line(turns.map(enemyTotal), "spark-them"));
}

function turnTable(turns) {
  return el("table", { class: "turns" },
    el("thead", {}, el("tr", {},
      ["turn", "you", "enemies", "actions"].map((h) => el("th", {}, h)))),
    el("tbody", {}, turns.map((t) => el("tr", {},
      el("td", { class: "tnum" }, t.turn),
      el("td", {}, `HP ${t.hp ?? "?"}${t.block ? " · block " + t.block : ""} · energy ${t.energy}`),
      el("td", {}, t.enemies.map((e) => el("div", {},
        `${e.name} ${e.hp}/${e.max_hp}${e.block ? " [block " + e.block + "]" : ""} — ${e.intent}`))),
      el("td", {}, t.actions.map((a) => el("div", {
        class: "act-line" + (a.forced ? " forced" : "") + (a.invalid ? " had-invalid" : ""),
      }, a.text + (a.invalid ? ` (${a.invalid} rejected first)` : ""))))))));
}

function decisionView(d) {
  const details = el("details", { class: "decision" + (d.forced ? " forced" : "") });
  const what = d.forced
    ? `FORCED (${d.forced}) → ${d.command || "?"}`
    : (d.action || d.command || "?");
  const meta =
    ` · ${d.rounds} rounds` +
    (d.lookups ? ` · ${d.lookups} lookups` : "") +
    (d.invalid ? ` · ${d.invalid} invalid` : "") +
    (d.latency_ms != null ? ` · ${(d.latency_ms / 1000).toFixed(1)}s` : "") +
    ` · ${num(d.usage.prompt + d.usage.completion)} tok` +
    (d.usage.reasoning ? ` (${num(d.usage.reasoning)} reasoning)` : "");
  details.append(el("summary", {},
    el("span", { class: "dnum" }, "#" + d.index),
    el("span", { class: "dscreen" }, d.screen || "?"),
    " " + what,
    el("span", { class: "meta" }, meta)));
  details.append(el("div", { class: "events" }, d.events.map(eventView)));
  return details;
}

function eventView(ev) {
  switch (ev.kind) {
    case "state": return el("pre", { class: "ev state" }, ev.text);
    case "reasoning": return el("div", { class: "ev reasoning" }, ev.text);
    case "text": return el("div", { class: "ev say" }, ev.text);
    case "call": return el("div", { class: "ev call" }, "→ " + ev.text);
    case "rejection": return el("pre", { class: "ev rejection" }, ev.text);
    default: return el("pre", { class: "ev result" }, ev.text);
  }
}

/* ---------- assemble ---------- */

document.body.append(themeToggle());
document.getElementById("app").append(el("main", {},
  header(),
  overview(),
  deckView(),
  behaviorView(),
  floorsView(),
  el("footer", { class: "dim small" }, "Generated by sts-bench · python -m sts_bench.report")));

if (DATA.run.verdict === "DEFEAT" && DATA.floors.length) {
  const last = document.getElementById("floor-" + DATA.floors[DATA.floors.length - 1].floor);
  if (last) last.open = true;
}
