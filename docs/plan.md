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

## M5 — Runner, baselines, report (MVP complete)

**Goal:** the spec's MVP — 5 fixed Ironclad A0 seeds, baselines vs LLM, a report.

Files: `runner/async_runner.py`, `runner/seeds.py`, `runner/metrics.py`, `runner/reports.py`, `agents/heuristic.py` (random-legal + scripted baselines).

1. Seed suites as data (`smoke: 5 seeds` first).
2. Runner v1 can be sequential (one game instance, N runs back to back); make the interface async-shaped so multi-instance is a config change, not a rewrite.
3. Multi-instance: each game's relay connects to the same harness server; session routing by connection. Load-test 2–3 instances for memory before promising more.
4. Metrics from trajectories: floor reached, win/loss, invalid-action rate, forced actions, latency, tokens, cost, observation-tool usage. Report as a markdown table per (model, scaffold, seed suite).

**Accept:** one command runs the smoke suite for {random baseline, scripted baseline, LLM zero-shot} and emits a comparison report.

## M6 — Scaffold & tool ablations

**Goal:** the experiment axes: turn-script mode, advisor tools, reflection/planning.

1. Stateless vs floor-stateful: same model, same seeds, `--agent stepwise` vs the M4 default — the first scaffold ablation, already free once M4 lands. Likewise reasoning effort sweeps (`--reasoning-effort` off/low/medium) per model.
2. Turn-script agent: model emits an ordered play list; harness executes with per-step revalidation; halt-and-return on invalidation; log script completion rate.
3. Card/relic DB (`tools/card_db.py`, `relic_db.py`) for `inspect_*` tools — source from a community data dump, pinned to game version.
4. Reflection/planning scaffold; advisor tools behind config flags.
5. Config system (one TOML/YAML per experiment: model, scaffold, tools, suite) so runs are reproducible by config hash.

**Accept:** same seed suite run under ≥2 scaffolds produces a report separating scaffold effect from model effect.

## M7 — Training exports (pre-RLVR)

**Goal:** trajectories → training data; close the loop described in the spec.

1. `export_sft.py`: chat-format SFT samples from winning/high-reward runs.
2. `export_preference.py`: preference pairs (e.g., validated vs rejected actions; later, outcome-contrasted decisions).
3. Re-benchmark a served open model (vLLM/Ollama behind the same provider) to validate the teacher/student symmetry claim.

RLVR itself (gym wrapper, simulator promotion) is the next project iteration, per the spec — M1's `Env` and M4's versioned rewards are the prep work.

## Cross-cutting rules

- **Tests ride on fixtures.** Captured real payloads in `tests/fixtures/`; parsing, validation, translation, and serialization are all unit-testable without a game running. The live game is only needed for M1/M3/M5 acceptance checks.
- **Never block on the game silently.** Every wait has a timeout and lands in the protocol log.
- **One new concept per milestone lands in the spec** if it changes the design (the spec is the source of truth; this plan is the schedule).

## Risks to watch first

- spirecomm is old (Python 2-era idioms, possibly stale vs current game/mod versions) — hence M1 writes the connection layer fresh and demotes spirecomm to reference material.
- Decision-point detection (M1.3) is the classic time sink: screen transitions, `WAIT` semantics, and mid-combat state churn. Budget real time there; everything downstream assumes it's solid.
- Token cost of the cursory view + observation calls compounds over a ~50-floor run. Measure from M3 onward, not at the end.
