# Implementation Plan

Companion to `spec.md`. Milestones are ordered so every one ends with something runnable; each lists its files, tasks, and an acceptance check. Target layout is `src/sts_bench/` per the spec's architecture section.

## Current state (M0 — done)

- `main.py`: spirecomm's `SimpleAgent` plays full runs through CommunicationMod (proves protocol + setup).
- `relay.py` / `console.py`: mod-launched byte relay → TCP socket → long-lived harness process; manual REPL with a readable state digest.
- Protocol logging to `logs/latest.log`.

Everything below builds on the relay pattern: the harness is a socket server the game connects to.

## M1 — Env layer: own the game loop

**Goal:** a blocking, agent-facing `Env` with `reset(seed, character, ascension)`, `step(action)`, `legal_actions()`, hiding WAIT/STATE/transition noise. This is the foundation for everything (and later, the RL interface).

Files: `src/sts_bench/env/base.py`, `env/communication_mod.py`, `env/connection.py` (socket server + per-connection protocol session).

1. Move to `src/` layout; keep `relay.py` at repo root (it's what SpireConfig points at), turn `console.py` into `python -m sts_bench.console`.
2. Write the connection layer fresh against the raw protocol (use spirecomm's `Coordinator` as a *reference*, not a dependency, for the stdin/stdout assumptions it bakes in — it wants to own the process's stdio, which fights the socket model). Keep spirecomm as a reference for state-field semantics.
3. Decision-point detection: consume state messages until `ready_for_command` and a stable screen; issue `WAIT`/`STATE` internally; surface exactly one state per decision.
4. `reset`: drive from any screen back to main menu / abandon, then `START <class> <ascension> <seed>`.
5. Error handling: CommunicationMod's `{"error": ...}` becomes a typed result, never an exception that kills the run.

**Accept:** a scripted loop (`python -m sts_bench.smoke`) calls `reset` + `step` with hardcoded/random legal choices and finishes floor 1 combat on a fixed seed, twice in a row without manual help.

*Status: **done.** Code complete (`env/connection.py`, `env/base.py`, `env/communication_mod.py`, `smoke.py`); 13 unit tests against a scripted fake mod pass; live acceptance passed 2026-06-09 (`smoke --floors 2` reached floor 2 in 107 steps against the real game).*

## M2 — State & action schemas, validation, translation

**Goal:** typed boundary between the game and everything else.

Files: `state/schema.py`, `state/serialize.py`, `actions/schema.py`, `actions/validate.py`, `actions/translate.py`.

1. Pydantic models for the state JSON (player, monsters, cards, potions, relics, map, screen variants). Capture *real* payloads from M1 runs into `tests/fixtures/` and unit-test parsing against them — this is the cheapest place to find protocol surprises.
2. Typed `Action` union mirroring the spec's action tools.
3. Validator: action → accept/reject against `available_commands` + screen state, with a human-readable rejection reason (this string goes back to the model as corrective feedback).
4. Translator: accepted action → CommunicationMod command string.
5. `serialize.py`: the **cursory view** — compact text/markdown digest (floor, HP, gold, screen, energy, hand with costs/targets, enemy HP/intents, choice list). Start from `console.py`'s `format_state`, which is already close. Measure its token count; it's a tracked artifact.

**Accept:** fixture-driven unit tests pass; smoke loop from M1 now runs on typed actions through validate→translate.

*Status: code complete. `state/schema.py` (strict Pydantic, `extra="forbid"`, modeled screens: NONE/EVENT/MAP/CARD_REWARD/COMBAT_REWARD/GRID; unmodeled screens raise `StateParseError` carrying the raw payload), `state/serialize.py` (cursory view: plain text in XML section tags, ~450 chars vs ~23KB raw), `actions/` (typed schema, validator with model-facing rejection reasons, wire translator), `harvest.py` (structural-signature dedup), smoke runs parse→action→validate→translate with raw fallback + `logs/unparsed/` capture. 53 tests. Remaining: a live smoke run to confirm, which will also capture shop/rest/chest/game-over states as they're hit.*

## M3 — Provider + first LLM agent (single combat)

**Goal:** an LLM wins or completes one combat via tool calls. The spec's MVP heart.

Files: `providers/base.py`, `providers/openai_compat.py`, `agents/base.py`, `agents/zero_shot.py`, `tools/registry.py`, `tools/schemas.py`.

1. `OpenAICompatProvider`: chat completions with tool calling; `base_url`/`model`/key from env/config; retries, timeouts, token usage capture.
2. Tool registry: action tools + observation tools (`get_deck`, `get_map`, `get_relics`, `get_potions`, piles, `get_legal_actions`). Observation tools answer from the already-held state — no extra game round-trips.
3. Zero-shot stepwise agent: system prompt + cursory state → model loops on observation tools → must end with exactly one action tool; invalid actions get the validator's rejection appended and a bounded number of retries (then fall back to a safe legal action, logged as a forced action).
4. Defer `inspect_card`/`inspect_relic` (needs a card DB) to M6 unless trivial.

**Accept:** one fixed-seed Ironclad A0 run where the LLM plays at least the first combat end to end; transcript shows observation calls and legal actions.

*Status: code complete. `providers/` (OpenAI-compat chat completions over stdlib urllib — no SDK; retries on 429/5xx; `from_env` resolves Anthropic/OpenAI/local-Ollama backends), `tools/` (registry generates action-tool schemas from the Pydantic action models; 8 observation tools answer from held state, incl. the floor-by-floor `get_map` adjacency renderer), `agents/zero_shot.py` (fresh conversation per decision; observation loop; validator rejections fed back; bounded budgets, then a logged forced fallback), `play.py` live entry point (`uv run python -m sts_bench.play`). `inspect_*` deferred to M6 as planned.*

*First live run (2026-06-10, gpt-5.4) surfaced two issues, both fixed: (1) `Power` was missing the mod's conditional extras (`just_applied`/`damage`/`misc`/`card`), so the first combat fell to the scripted fallback the moment Bash applied Vulnerable — schema extended, harvested state kept as fixture `none-5.json`; (2) the stateless agent ping-ponged forever between shop room and shop screen, since identical states produce identical prompts — the agent now carries a short `<recent_decisions>` trail across decision points (still one fresh conversation per decision), and a runner `LoopGuard` forces the scripted fallback when the same command is chosen from a byte-identical state 3×, counted as forced.*

***ACCEPTED** (2026-06-10): clean acceptance run, gpt-5.4, Ironclad A0 seed STSBENCH1 — 311 decisions, 0 forced, 0 unparsed states, dead floor 23 (act 2, Slaver/Taskmaster gauntlet at 22 HP), score 214, ~389k tokens in ~10.5 min. The shop that looped in run 1 resolved in single-round decisions via the trail; the loop guard never fired. Notable model behavior for M5/M6: 91% of decisions made zero observation lookups (49 total: map/deck/relics/potions; the pile tools and `get_legal_actions` were never called), ~22 completion tokens per decision (no visible deliberation). The protocol log now interleaves four channels (`>>`/`<<` game wire verbatim, `>m`/`<m` model traffic with the system prompt logged once, `--` narrative with floor/combat landmarks and readable decisions); logfile creation is deferred until the relay connects. 105 tests.*

*Post-acceptance additions (2026-06-10, recorded here since they extend M3's surface): provider trio behind one `ModelProvider` protocol — `chat` (universal compat), `responses` (OpenAI native: reasoning summaries + reasoning carried across tool rounds via echoed items), `anthropic` (native Messages: thinking blocks, prompt-cache breakpoint on the system block, structured tool inputs); `--api auto` picks the native surface per backend; `--reasoning-effort` maps to each provider's effort knob (and turns adaptive thinking on for Claude) — without it, gpt-5-class models do essentially no reasoning (verified live: reflex baseline vs effort-low run with visible traces); reasoning text logs as `<m (reasoning)` lines, hidden reasoning spend as `(of which N reasoning)`; log header records model/api/effort/seed; README added. 129 tests.*

## M4 — Floor conversations + trajectory logging

**Goal:** the context unit, eval unit, and storage unit all become the **floor** (see spec: Context Boundaries). A `FloorAgent` keeps one conversation per floor; every floor produces a self-contained packet; every decision produces a structured record pointing into it.

Files: `agents/floor.py`, `trajectory/schema.py`, `trajectory/jsonl.py`, `env/rewards.py`.

1. `FloorAgent` (new default; zero-shot kept behind `--agent stepwise` as the ablation baseline): one conversation per floor; each decision opens by closing the previous action's tool call (executed / rejected+reason / fallback played), then appends the fresh digest; reset on floor change with one carried-over summary line; `<recent_decisions>` and `agent.record()` retire from the default path.
2. Runner/logging deltas: `>m`/`<m` logging emits only each decision's new conversation slice (never re-dump the transcript — O(n²) otherwise); decision brackets gain nothing, but a `--` floor summary line lands at each boundary (decisions, tokens incl. cache reads, HP/gold delta, pick made).
3. Trajectory JSONL, one file per run, atomic appends, Pydantic with `schema_version`: run record (config + outcome + totals), floor record (the packet: full conversation, boundary raw states, scorecard, versioned reward), decision record (message index range — not a copy — plus action/validation/usage/latency). The store-once invariant gets its own test.
4. Reward extraction from floor-boundary state deltas (HP, gold, floor advanced, combat won, run won) — versioned (`reward_spec_version`); raw boundary states stored so rewards are recomputable.

**Accept:** replay a logged run from the JSONL alone (re-render each floor's conversation and each decision), and verify the packet property: the floor record's conversation matches what the wire saw, with no message stored twice.

*Status: items 1–2 landed 2026-06-11 (`FloorAgent` default, `--agent stepwise` baseline, per-decision delta logging, `--` floor summaries; the loop-body moved to a shared `agents/base.py`). First live run (gpt-5.4-mini, responses, effort low) cut invalid-action decisions 11.8%→2.9% and reasoning spend ~7x vs the paired zero-shot run, then died on floor 14: it took a potion reward onto a full belt, which CommunicationMod never returns ready from. That run drove a player-parity pass (see spec: zero-click player parity): reward screens render as claim-all loot lists with slot counts and belt-full affordances, HAND_SELECT/REST/shop say what their remaining moves mean, the commands line speaks tool vocabulary, the validator rejects potion-take/buy on a full belt, step timeouts return an error result instead of crashing (totals print on every exit), and M6's card DB was pulled forward — pinned spire-archive snapshot (360 cards), printed text on card rewards and shop wares, and a once-per-combat `<deck_reference>` injected by the floor agent. Also fixed en route: upgrade marks double-rendered ("Strike++"). Items 3–4 (trajectory JSONL, floor rewards) remain.*

*Items 3–4 landed 2026-06-11. `trajectory/` (record schemas with a `record` discriminator + `schema_version`, all three kinds in one JSONL per run; append-only store, whole-line writes flushed per append so the file is valid mid-run and after a crash; `RunRecorder` accumulates each floor's conversation from decision transcript deltas — for the floor agent the deltas concatenate to exactly the wire conversation, which is the packet property — and writes decisions as they commit, floor records at boundaries, the run record last from a `finally`, so a missing run record itself marks a death mid-flight). `env/rewards.py` (spec v1: floor_advanced +1, combat_won +2, run_won +50, HP delta ×5/max_hp; terminal value only — gold was cut after review since rewarding its delta punishes spending it at shops; instrumental resources stay scorecard metrics; components stored per signal; a floor's exit boundary is the *next* floor's first state — the state that proves the advance — so rewards recompute from the stored raw pair alone). `replay.py` re-renders a run from the JSONL with no game or model (`uv run python -m sts_bench.replay <file> [--floor N]`) and verifies the packet property on every floor: decision index ranges must tile the stored conversation — gaps, overlaps, and orphaned messages all flag and exit nonzero. The trajectory file pairs with the protocol log by name (`logs/trajectories/play-<stamp>.jsonl`). Run records carry prompt/tool-schema hashes so runs compare only when the model saw the same surfaces. `Reward` moved to the env layer (spec: reward extraction belongs to the env, and it breaks the env↔trajectory import cycle); decision latency now measured; `TokenUsage.cache_read_tokens` reserved (providers don't report cache reads yet — small follow-up). 228 tests.*

***ACCEPTED** (2026-06-11, harness-level): a run recorded through the real `FloorAgent` (scripted provider, two floors incl. a boundary) replays from the JSONL alone — run header, floor scorecards + rewards, every decision with its conversation slice — and the wire-equivalence test asserts the stored floor conversation equals the provider's final request + response byte for byte, with the store-once test confirming each message lands in the file exactly once. Live-game spot check (a real run, then `replay` on its trajectory) still worth doing on the next play session.*

## M5 — Runner, baselines, report (MVP complete)

**Goal:** the spec's MVP — 5 fixed Ironclad A0 seeds, baselines vs LLM, a report.

Files: `runner/async_runner.py`, `runner/seeds.py`, `runner/metrics.py`, `runner/reports.py`, `agents/heuristic.py` (random-legal + scripted baselines).

1. Seed suites as data (`smoke: 5 seeds` first).
   Ride-along experiment while live: combat-start states sometimes arrive before intents roll (`intent DEBUG` → "not yet revealed"; 21 of the 2026-06-11 run's digests) though the GUI always shows turn-1 intents — try one `state` re-poll from the env when a ready state carries a DEBUG intent, and verify it resolves.
2. Runner v1 can be sequential (one game instance, N runs back to back); make the interface async-shaped so multi-instance is a config change, not a rewrite.
3. Multi-instance: each game's relay connects to the same harness server; session routing by connection. Load-test 2–3 instances for memory before promising more.
4. Metrics from trajectories: floor reached, win/loss, invalid-action rate, forced actions, latency, tokens, cost, observation-tool usage. Report as a markdown table per (model, scaffold, seed suite).

**Accept:** one command runs the smoke suite for {random baseline, scripted baseline, LLM zero-shot} and emits a comparison report.

*Status: code complete 2026-06-12. `agents/heuristic.py` (random-legal: uniform over exactly what the validator accepts, candidates enumerated from the state's index spaces and filtered through the one legality oracle the LLM agents face, RNG seeded by the run seed; scripted: the smoke policy promoted to the agent protocol, falling through the raw advance order when its pick is rejected — e.g. choice 0 is a potion onto a full belt). `runner/seeds.py` (smoke = STSBENCH1–5, Ironclad A0). `runner/metrics.py` (per-run metrics from trajectory records alone — verified against the M4 live run's JSONL — and per-configuration aggregation keyed on model/scaffold/effort *plus* the prompt/tool hashes, so runs that saw different surfaces never average; `hash_drift` explains unmerged rows). `runner/reports.py` (markdown: configuration table, floor-by-seed grid, per-run table; cost only from an explicit pricing file, never a stale guess). The per-run loop moved out of `play.py` into `runner/session.py` (`play_run` + caller-owned `RunTally`, so a crashed run still reports partials), with `play.py` now just the single-run front end of the same loop; `model_traffic`/`reasoning_note` live in `protocol_log.py` (replay imports there too), the scaffold registry in `agents.SCAFFOLDS`, `auto_api` in `providers`. `runner/async_runner.py` + `python -m sts_bench.bench`: jobs = agents x seeds on an asyncio queue; each connected game instance is a worker, so multi-instance is `--instances N` (untested live beyond 1); per job its own protocol log (via a LogRouter, since the env's log hook is fixed at construction) and trajectory file; a failed job costs one run unless the game is left mid-run, which stops the worker rather than corrupt every following job. Harness-level test drives a 2x2 suite over a scripted fake mod through the real socket/env/recorder path. Sessions compose: `bench --report-from <jsonl...>` regenerates one table over any mix of past trajectories (baselines one day, model runs another, the M4 play run included), since every metric reads from the files alone. The M5.1 intent re-poll was already in the env (`_intent_rolling` + bounded re-probe), unit-tested; live confirmation rides along with the acceptance session. 325 tests.*

*First live session (2026-06-12, random baseline) caught a new mod quirk on STSBENCH3's Neow event: `cancel` on a grid screen deselects the chosen card but CommunicationMod never flips `ready_for_command` (no state-change trigger fires), so the step timed out holding the pre-click state and the fallback chose from stale commands (`confirm`) — fatal, suite stopped after 3 runs. Fix in the env: the mid-timeout `state` nudge's answer — not ready, but true — is now adopted on timeout (`StepTimeout.last_state`), so recovery picks from the game as it stands; worst case the random agent re-cancels until the loop guard forces scripted `confirm`. Regression test reproduces the live wire sequence.*

*Second live session caught the scripted agent in a broke shop on STSBENCH4: after buying the purge (gold 24), the mod drops `choose` from the shop screen entirely, so the agent's advance fallback `leave` lands on SHOP_ROOM whose only choice re-enters — a cycle no memoryless policy can exit. The loop guard tripped as designed but its forced fallback called the same scripted policy (None on a choose-less screen), then the raw default degenerated to `state` no-ops: 938 forced decisions to the max_decisions budget, game left mid-run, worker stopped (so the budget and fatal-stop both worked; the fallback was the bug). Fix in the session loop: `forced_command` — budget exhaustion still gets the scripted move, but a loop-guard trip skips the scripted policy (it can BE the loop) and answers from the raw advance family (`end/proceed/leave/...`), the one guaranteed to change screens; SHOP_ROOM's `proceed` walks the run out in ~5 decisions. Unit tests on the forced matrix + an integration test replaying the exact shop cycle through `run_suite`. 331 tests.*

*Baseline leg ACCEPTED live (2026-06-12): `bench --suite smoke --agents random,scripted` ran 10 of 10 jobs unattended, 0 errors — both prior failure sites cleared (random played through the Neow grid; scripted escaped the broke shop via forced `leave` then `proceed`, 88 decisions, 2 forced). Between-run reset-to-menu held for all 9 boundaries. Calibration the suite exists for: random mean floor 5 (best 10), scripted mean floor 10 (best 16), all defeats — so the M4 gpt-5.4-mini floor-23 run sits well above reflexes. Spot-checks: `replay` verifies the packet property on a bench trajectory (7 floors, 88 decisions, every message stored once); zero "not yet revealed" intents across all five scripted logs (21 in the 2026-06-11 run), confirming the env's intent re-probe live.*

***ACCEPTED** (2026-06-12): LLM leg ran the same day — 5 of 5 floor-agent runs (gpt-5.4-mini, responses, effort low) unattended, 0 errors, 0 forced; one `--report-from` over both sessions produced the three-row table the milestone exists for: random floor 5 / scripted 10 / gpt-5.4-mini 21.6 mean (best 33 — act 3, STSBENCH2), invalid decisions 3.1%, ~1.45M prompt tokens per run. The MVP (spec) is complete. The serialization-v2 evidence gathered during the run (bare `Hex`, `Split -1`, the shop affordance trace) is spec'd under M6 item 3.*

## M6 — Scaffold & tool ablations

**Goal:** the experiment axes: turn-script mode, advisor tools, reflection/planning.

1. Stateless vs floor-stateful: same model, same seeds, `--agent stepwise` vs the M4 default — the first scaffold ablation, already free once M4 lands. Likewise reasoning effort sweeps (`--reasoning-effort` off/low/medium) per model.
2. Turn-script agent: model emits an ordered play list; harness executes with per-step revalidation; halt-and-return on invalidation; log script completion rate.
3. ~~Card/relic/potion DBs~~ — all three landed early during M4 (see its status note): pinned spire-archive snapshots in `tools/data/`, text on every offering surface, and the once-per-combat briefing (relic bar + potion belt + deck reference). Remaining here: the `inspect_*` observation tools themselves, now a thin lookup over the DBs — plus the **serialization v2** batch below.

   **Serialization v2** (one batch, one prompt-hash bump; the 2026-06-12 gpt-5.4-mini smoke run is the control arm — same model, same seeds, v1 vs v2 is the spec's "state representation quality" ablation):
   - **Powers DB**: pull spire-archive's `powers.json` (wire to the keyword DB, pinned during M4). Powers are the only entity without one, and the smoke run showed both costs live: `Hex 1` reached the model bare and it recovered *from pretrained knowledge* of the Chosen's debuff — the benchmark silently rewarding memorized game knowledge over in-context play (a human reads the tooltip; parity violation) — and Slime Boss rendered as `Split -1`, the wire's no-stacks sentinel shown literally, indistinguishable from a real negative stack (`Strength -1` is meaningful) and readable as a depleted resource. Render: stackless powers drop the number (`Split`), stackable keep it; definitions injected **on first appearance per combat** (the deck-reference pattern — floor conversation retains it, token cost bounded by the handful of powers a fight introduces); unknown powers fall back to today's rendering and get logged for harvesting.
   - **Observation-tool affordance wording**: a shop reasoning trace showed the model declining lookups it was entitled to — `ACTION_PROTOCOL`'s "exactly one action tool call" shadows the "observation tools are free" sentence under low reasoning effort, and both the view's commands line and `get_legal_actions` enumerate only action tools, so a model asking "what may I call here" sees a list excluding `get_deck`. M3 already measured the symptom: 91% of decisions made zero lookups; the pile tools were never called. Fix is wording only: "you may call any number of observation tools first; finish with exactly one action tool call", and the commands line gains "+ observation tools (always available)".
4. Reflection/planning scaffold; advisor tools behind config flags.
5. Config system (one TOML/YAML per experiment: model, scaffold, tools, suite) so runs are reproducible by config hash.

**Accept:** same seed suite run under ≥2 scaffolds produces a report separating scaffold effect from model effect.

## M7 — Training exports (pre-RLVR)

**Goal:** trajectories → training data; close the loop described in the spec.

1. `export_sft.py`: chat-format SFT samples from winning/high-reward runs.
2. `export_preference.py`: preference pairs (e.g., validated vs rejected actions; later, outcome-contrasted decisions).
3. Re-benchmark a served open model (vLLM/Ollama behind the same provider) to validate the teacher/student symmetry claim.

RLVR itself (gym wrapper, simulator promotion) is the next project iteration, per the spec — M1's `Env` and M4's versioned rewards are the prep work.

## Parked — act 4 / heart runs (revisit when any model reliably reaches act 3)

Decided 2026-06-11, during M4 live-run review: the MVP scores an act-3 boss kill as a win (the game itself calls it victory without keys); heart runs become a later suite dimension. Information groundwork is in: rest options carry their button text (recall = Ruby Key), key rewards explain themselves, the run line shows held keys when the wire sends them. Two blockers wait at the mod boundary, both confirmed against CommunicationMod master:

1. **Installed CommunicationMod predates `keys` serialization** — master sends `keys` (ruby/emerald/sapphire) in every state, our 2026-06-11 run never did. Upgrading the jar lights up the existing `keys 2/3 (...)` run-line support with no harness change.
2. **Burning elites are invisible on the wire** — map nodes serialize only symbol/x/y/edges; the game's `MapRoomNode.hasEmeraldKey` is never sent, so no model can deliberately route to the emerald key (a parity violation in the mod: the flame is plainly visible to a human). Fix is a one-line fork of `convertMapRoomNodeToJson`; the spec blesses Java at the mod boundary. **Grow the `MapNode` schema (optional field) in the same change** — it is `extra="forbid"`, so a patched mod fails parsing until the schema knows the field.

Also waiting there: act 4 door/entry screens are unmodeled (designed recovery path: unparsed capture → fixture → schema), and the reward spec can grow a `heart_kill` component when heart runs are scored.

## Cross-cutting rules

- **Tests ride on fixtures.** Captured real payloads in `tests/fixtures/`; parsing, validation, translation, and serialization are all unit-testable without a game running. The live game is only needed for M1/M3/M5 acceptance checks.
- **Never block on the game silently.** Every wait has a timeout and lands in the protocol log.
- **One new concept per milestone lands in the spec** if it changes the design (the spec is the source of truth; this plan is the schedule).

## Risks to watch first

- spirecomm is old (Python 2-era idioms, possibly stale vs current game/mod versions) — hence M1 writes the connection layer fresh and demotes spirecomm to reference material.
- Decision-point detection (M1.3) is the classic time sink: screen transitions, `WAIT` semantics, and mid-combat state churn. Budget real time there; everything downstream assumes it's solid.
- Token cost of the cursory view + observation calls compounds over a ~50-floor run. Measure from M3 onward, not at the end.
