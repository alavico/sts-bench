"use strict";
/* Pure renderer: all data shaping happened before embedding. The page reads
   the JSON payload and builds the DOM; nothing here re-derives game facts. */

const DATA = JSON.parse(document.getElementById("data").textContent);

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

/* ---------- room styling ---------- */

const ROOM = {
  MonsterRoom: ["monster", "#e06c5b", "M"],
  MonsterRoomElite: ["elite", "#b07cf0", "E"],
  MonsterRoomBoss: ["boss", "#ff5757", "B"],
  EventRoom: ["event", "#5bc0de", "?"],
  NeowRoom: ["start", "#9aa0a6", "N"],
  ShopRoom: ["shop", "#e8c860", "$"],
  RestRoom: ["rest", "#7ed87e", "R"],
  TreasureRoom: ["chest", "#f0a850", "T"],
  TreasureRoomBoss: ["boss chest", "#f0a850", "T"],
};

function room(type) {
  const entry = ROOM[type];
  if (entry) return { label: entry[0], color: entry[1], symbol: entry[2] };
  return { label: type || "?", color: "#9aa0a6", symbol: "?" };
}

const SYMBOL_COLOR = {
  M: "#e06c5b", E: "#b07cf0", $: "#e8c860", R: "#7ed87e",
  T: "#f0a850", "?": "#5bc0de", B: "#ff5757",
};

/* ---------- tooltip + navigation ---------- */

const tip = el("div", { class: "tooltip" });
tip.style.display = "none";
document.body.append(tip);

function showTip(evt, lines) {
  tip.replaceChildren(...lines.map((line) => el("div", {}, line)));
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

function openFloor(floorNo) {
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
    run.character && `${run.character} · ascension ${run.ascension}`,
    "seed " + (run.seed || "random"),
    run.api && run.api + " api" + (run.reasoning_effort ? `, effort ${run.reasoning_effort}` : ""),
    run.run_id,
  ].filter(Boolean).join("  ·  ");

  const usage = run.usage || sumFloorUsage();
  const latency = DATA.floors.reduce((sum, floor) => sum + (floor.latency_ms || 0), 0);
  const modelTime = latency >= 90000
    ? Math.round(latency / 60000) + " min"
    : Math.round(latency / 1000) + " s";
  const decisions = run.decisions ?? DATA.floors.reduce((sum, floor) => sum + floor.decisions.length, 0);

  const head = el("header", {},
    el("h1", {}, "sts-bench run report"),
    el("div", { class: "runline" },
      el("span", { class: "verdict " + run.verdict.toLowerCase() }, run.verdict + where),
      run.score != null ? chip("score", num(run.score)) : null),
    el("div", { class: "configline" }, config),
    el("div", { class: "stats" },
      chip("floors", num(DATA.floors.length)),
      chip("decisions", num(decisions)),
      run.forced ? chip("forced", num(run.forced)) : null,
      chip("prompt tok", num(usage.prompt)),
      chip("completion tok", num(usage.completion)),
      usage.reasoning ? chip("reasoning tok", num(usage.reasoning)) : null,
      chip("model time", modelTime)));

  if (run.missing) {
    head.append(el("div", { class: "warn" },
      "no run record: the run died mid-flight; the floors below are what completed"));
  }
  if (run.verdict === "DEFEAT" && DATA.floors.length) {
    const last = DATA.floors[DATA.floors.length - 1];
    head.append(el("div", { class: "death-link" },
      el("a", {
        href: "#floor-" + last.floor,
        onclick: (e) => { e.preventDefault(); openFloor(last.floor); },
      }, `→ post-mortem: floor ${last.floor} (${room(last.type).label})`)));
  }
  return head;
}

function chip(label, value) {
  return el("span", { class: "chip" }, el("b", {}, value), label ? " " + label : "");
}

/* ---------- overview charts ---------- */

function section(title, ...body) {
  return el("section", {}, el("h2", {}, title), ...body);
}

function overview() {
  const floors = DATA.floors;
  if (!floors.length) return section("Run at a glance", el("p", { class: "dim" }, "no floors recorded"));
  return section("Run at a glance",
    hpChart(floors),
    strip(floors, "gold at floor exit", (f) => f.exit.gold ?? 0, "#e8c860"),
    strip(floors, "tokens per floor", (f) => f.usage.prompt + f.usage.completion, "#6f87c0"),
    strip(floors, "decisions per floor (red = rejected first)",
      (f) => f.decisions.length, "#8b909b",
      (f) => f.decisions.reduce((sum, d) => sum + d.invalid, 0)));
}

/* Shared horizontal scale: the hp chart plots floors.length+1 points (every
   entry plus the final exit), and the strips center their bars on the same
   x positions so the columns line up. */
const CHART_W = 940, CHART_L = 42, CHART_R = 16;

function chartX(index, count) {
  return CHART_L + (index * (CHART_W - CHART_L - CHART_R)) / Math.max(count - 1, 1);
}

function hpChart(floors) {
  const H = 230, T = 16, B = 26;
  const pts = floors.map((f) => ({ hp: f.entry.hp ?? 0, max: f.entry.max_hp ?? 0, f, exit: false }));
  const last = floors[floors.length - 1];
  pts.push({ hp: last.exit.hp ?? 0, max: last.exit.max_hp ?? 0, f: last, exit: true });
  const top = Math.max(...pts.map((p) => p.max), 1);
  const x = (i) => chartX(i, pts.length);
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const hpPath = pts.map((p, i) => (i ? "L" : "M") + x(i) + " " + y(p.hp)).join("");
  const maxPath = pts.map((p, i) => (i ? "L" : "M") + x(i) + " " + y(p.max)).join("");

  return sv("svg", { viewBox: `0 0 ${CHART_W} ${H}`, class: "chart" },
    [0, 0.5, 1].map((frac) => {
      const v = Math.round(top * frac);
      return [
        sv("line", { x1: CHART_L, x2: CHART_W - CHART_R, y1: y(v), y2: y(v), class: "grid" }),
        sv("text", { x: CHART_L - 6, y: y(v) + 4, class: "axis", "text-anchor": "end" }, v),
      ];
    }),
    sv("text", { x: CHART_W - CHART_R, y: 11, class: "legend", "text-anchor": "end" },
      "HP at floor entry — dotted: max HP — hover a floor, click to open it"),
    sv("path", { d: maxPath, class: "maxhp" }),
    sv("path", { d: hpPath, class: "hp" }),
    pts.map((p, i) => p.exit ? null
      : sv("text", { x: x(i), y: H - 8, class: "axis", "text-anchor": "middle" }, p.f.floor)),
    pts.map((p, i) => marker(p, x(i), y(p.hp))));
}

function marker(point, cx, cy) {
  const floor = point.f;
  const info = room(floor.type);
  return sv("circle", {
    cx, cy,
    r: point.exit ? 4 : 5.5,
    class: "mark",
    fill: point.exit ? "none" : info.color,
    stroke: info.color,
    onclick: () => openFloor(floor.floor),
    onmousemove: moveTip,
    onmouseenter: (e) => showTip(e, floorTipLines(floor)),
    onmouseleave: hideTip,
  });
}

function floorTipLines(floor) {
  const sc = floor.scorecard;
  const lines = [
    `floor ${floor.floor} — ${room(floor.type).label}`,
    `HP ${floor.entry.hp}→${floor.exit.hp} (${signed(sc.hp_delta)})  ·  gold ${floor.entry.gold}→${floor.exit.gold}`,
  ];
  if (sc.combat_turns) lines.push(`${sc.combat_turns} combat turns`);
  const gains = [...sc.cards_gained, ...sc.relics_gained, ...sc.potions_gained];
  if (gains.length) lines.push("gained: " + gains.join(", "));
  if (floor.reward) lines.push(`reward ${floor.reward.total > 0 ? "+" : ""}${floor.reward.total}`);
  lines.push(`${floor.decisions.length} decisions · ${num(floor.usage.prompt + floor.usage.completion)} tokens`);
  return lines;
}

function strip(floors, title, value, color, overlay) {
  const H = 86, T = 18, B = 6;
  const values = floors.map(value);
  const top = Math.max(...values, 1);
  const count = floors.length + 1; // align bars with the hp chart's points
  const step = (CHART_W - CHART_L - CHART_R) / Math.max(count - 1, 1);
  const barWidth = Math.max(4, step * 0.55);
  const y = (v) => T + (1 - v / top) * (H - T - B);

  const bars = floors.map((floor, i) => {
    const cx = chartX(i, count);
    const items = [sv("rect", {
      x: cx - barWidth / 2, y: y(values[i]),
      width: barWidth, height: Math.max(0, H - B - y(values[i])),
      fill: color, class: "bar",
      onclick: () => openFloor(floor.floor),
      onmousemove: moveTip,
      onmouseenter: (e) => showTip(e, [`floor ${floor.floor}: ${num(values[i])}`]),
      onmouseleave: hideTip,
    })];
    if (overlay) {
      const over = overlay(floor);
      if (over) {
        items.push(sv("rect", {
          x: cx - barWidth / 2, y: y(over),
          width: barWidth, height: Math.max(0, H - B - y(over)),
          fill: "#ff5757", class: "bar",
        }));
      }
      if (floor.decisions.some((d) => d.forced)) {
        items.push(sv("text", { x: cx, y: 13, "text-anchor": "middle", class: "warn-mark" }, "⚠"));
      }
    }
    return items;
  });

  return sv("svg", { viewBox: `0 0 ${CHART_W} ${H}`, class: "chart strip" },
    sv("text", { x: CHART_L, y: 12, class: "strip-title" }, `${title} (max ${num(top)})`),
    bars);
}

/* ---------- act maps ---------- */

function mapsView() {
  if (!DATA.acts.length) return null;
  return section("The route", el("div", { class: "maps" }, DATA.acts.map(actMap)));
}

function actMap(act) {
  if (!act.nodes.length) {
    return el("div", { class: "map map-missing" }, `act ${act.act}: no map captured`);
  }
  const cols = Math.max(...act.nodes.map((n) => n.x)) + 1;
  const rows = Math.max(...act.nodes.map((n) => n.y)) + 1;
  const cw = 40, ch = 28, pad = 18, top = 40;
  const W = pad * 2 + cols * cw;
  const H = pad * 2 + rows * ch + top;
  const px = (v) => pad + v * cw + cw / 2;
  const py = (v) => H - pad - v * ch - ch / 2;

  const visited = new Map(act.path.filter((p) => !p.boss).map((p) => [p.x + "," + p.y, p]));
  const bossTaken = act.path.some((p) => p.boss);
  const bossX = W / 2, bossY = pad + 12;

  const edges = act.edges.map(([x1, y1, x2, y2]) =>
    sv("line", { x1: px(x1), y1: py(y1), x2: px(x2), y2: py(y2), class: "map-edge" }));

  const routePts = act.path.filter((p) => !p.boss).map((p) => px(p.x) + "," + py(p.y));
  if (bossTaken && routePts.length) routePts.push(bossX + "," + (bossY + 10));
  const route = routePts.length > 1
    ? sv("polyline", { points: routePts.join(" "), class: "route" })
    : null;

  const nodes = act.nodes.map((node) => {
    const hit = visited.get(node.x + "," + node.y);
    const color = SYMBOL_COLOR[node.symbol] || "#9aa0a6";
    return sv("g", {
      class: "map-node" + (hit ? " visited" : ""),
      onclick: hit ? () => openFloor(hit.floor) : null,
      onmousemove: hit ? moveTip : null,
      onmouseenter: hit ? (e) => showTip(e, [`floor ${hit.floor}`]) : null,
      onmouseleave: hit ? hideTip : null,
    },
      sv("circle", { cx: px(node.x), cy: py(node.y), r: hit ? 10 : 7, fill: hit ? color : "none", stroke: color }),
      sv("text", {
        x: px(node.x), y: py(node.y) + 3.5, "text-anchor": "middle",
        class: "map-symbol" + (hit ? " on" : ""),
      }, node.symbol || "?"));
  });

  return el("div", { class: "map" },
    el("h3", {}, "Act " + act.act),
    sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "act-map", style: `width:${W}px` },
      edges, route, nodes,
      sv("text", {
        x: bossX, y: bossY, "text-anchor": "middle",
        class: "boss-label" + (bossTaken ? " taken" : ""),
      }, "BOSS · " + (act.boss || "?"))));
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
      ? el("div", { class: "warn" }, "packet property VIOLATED — ", violations.join(" | "))
      : null,
    el("div", { class: "panels" },
      panel("actions", barList([...mix.entries()].sort((a, b) => b[1] - a[1]))),
      panel("card rewards", el("div", { class: "big-stat" },
        el("div", {}, el("b", {}, takes), " taken"),
        el("div", {}, el("b", {}, skips), " skipped"))),
      panel("observation lookups", lookups.size
        ? barList([...lookups.entries()].sort((a, b) => b[1] - a[1]))
        : el("div", { class: "dim" }, "none — the model never used a lookup tool"))),
    rejections.length
      ? panel(`rejected actions (${rejections.length})`,
        el("div", { class: "rejections" }, rejections.map((r) =>
          el("div", { class: "rej" },
            el("a", {
              href: "#floor-" + r.floor,
              onclick: (e) => { e.preventDefault(); openFloor(r.floor); },
            }, `floor ${r.floor} · #${r.decision}`),
            " — " + r.text))))
      : null);
}

function panel(title, body) {
  return el("div", { class: "panel" }, el("h3", {}, title), body);
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
        el("summary", {}, "system prompt (constant; shown once)"),
        el("pre", {}, DATA.system_prompt))
      : null,
    DATA.floors.map(floorView),
    DATA.orphans.map((o) => el("div", { class: "warn" },
      `floor ${o.floor}: ${o.decisions} decisions but no floor record (run died mid-floor)`)));
}

function floorView(floor) {
  const info = room(floor.type);
  const sc = floor.scorecard;
  const details = el("details", { class: "floor", id: "floor-" + floor.floor });

  const meta =
    ` HP ${floor.entry.hp}→${floor.exit.hp} (${signed(sc.hp_delta)}) · gold ${floor.entry.gold}→${floor.exit.gold}` +
    (sc.combat_turns ? ` · ${sc.combat_turns} turns` : "") +
    ` · ${floor.decisions.length} decisions · ${num(floor.usage.prompt + floor.usage.completion)} tok` +
    (floor.reward ? ` · reward ${floor.reward.total > 0 ? "+" : ""}${floor.reward.total}` : "");

  details.append(el("summary", {},
    el("span", { class: "sym", style: "background:" + info.color }, info.symbol),
    el("b", {}, `floor ${floor.floor}`), `· ${info.label}`,
    el("span", { class: "meta" }, meta)));

  const body = el("div", { class: "floor-body" });
  if (floor.violations.length) {
    body.append(el("div", { class: "warn" }, "packet violations: " + floor.violations.join(" | ")));
  }
  const gains = [
    ...sc.cards_gained.map((g) => ["card", g]),
    ...sc.relics_gained.map((g) => ["relic", g]),
    ...sc.potions_gained.map((g) => ["potion", g]),
  ];
  if (gains.length) {
    body.append(el("div", { class: "gains" },
      gains.map(([kind, name]) => el("span", { class: "chip gain " + kind }, name))));
  }
  if (floor.reward && Object.keys(floor.reward.components || {}).length) {
    body.append(el("div", { class: "dim small" },
      `reward ${floor.reward.spec}: ` +
      Object.entries(floor.reward.components)
        .map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`).join(", ")));
  }
  if (floor.turns) body.append(fightsView(floor.turns));
  body.append(el("div", { class: "decisions" }, floor.decisions.map(decisionView)));
  details.append(body);
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
  return el("div", { class: "fights" }, fights.map((fight) =>
    el("div", { class: "fight" }, fightSpark(fight), turnTable(fight))));
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

document.getElementById("app").append(el("main", {},
  header(),
  overview(),
  mapsView(),
  behaviorView(),
  floorsView(),
  el("footer", { class: "dim small" }, "generated by sts-bench · python -m sts_bench.report")));

if (DATA.run.verdict === "DEFEAT" && DATA.floors.length) {
  const last = document.getElementById("floor-" + DATA.floors[DATA.floors.length - 1].floor);
  if (last) last.open = true;
}
