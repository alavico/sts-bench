# sts-bench: LLM Slay the Spire Benchmark Spec

A model-agnostic harness for having LLMs play Slay the Spire end to end with minimal intervention. The immediate goal is to learn modern long-horizon LLM benchmarking; the practical end state is a data factory for gameplay trajectories, model comparisons, and eventually fine-tuning smaller agents.

The core interaction loop: at each decision point the agent receives a compact, cursory view of the game state, may query for additional information it could legally see (deck, draw/discard piles, potions, enemy intents, relic text, map), and then commits to actions — card plays in order during combat, plus pathing, card rewards, shops, and events between combats. The harness executes, the game advances, and the loop repeats for a whole run.

Python + `uv` is the right default. The benchmark harness, model adapters, trajectory logging, eval runner, and training exports are naturally Python. Keep Java only at the game/mod boundary.

## Locked Decisions

| Question | Decision |
| --- | --- |
| Primary backend | Real game + CommunicationMod by default. |
| Python game interface | Start from `spirecomm`; do not reinvent state parsing. |
| Headless backend | Optional secondary backend for CI/smoke tests or rollouts; not trusted for training data until proven faithful. |
| Action interface | Tool-calling plus server-side validation against the game's legal-action list. |
| Observation interface | Tiered: compact state summary by default, query tools for everything else the player could legally see. |
| Process model | Mod launches a thin relay; the harness is a long-lived socket server that owns the loop. |
| First model adapter | One OpenAI-compatible provider with configurable `base_url`, `model`, and API key. |
| Data strategy | Full structured trajectory logging from the beginning. |
| Scaling strategy | Async runner over multiple real-game instances; batch model inference through OpenAI-compatible serving when possible. |
| Next iteration | RLVR: expose the same env as an RL environment with verifiable rewards; design rewards now so they survive that transition. |

## Founding Questions

### 1. Headless clone or communication mod?

Use the real game through a communication mod first.

Two viable paths exist:

- **Real game + CommunicationMod**
  `CommunicationMod` is a Java mod loaded via ModTheSpire. It launches an external process and communicates through newline-delimited stdin/stdout messages. The model harness sends commands and receives stable game-state JSON.
  Source: https://github.com/ForgottenArbiter/CommunicationMod

- **Headless clone/simulator**
  A headless implementation can be faster and easier to parallelize. It is attractive for reinforcement learning, tree search, CI, and simulation tools. The risk is correctness drift: Slay the Spire has many cards, relics, events, random effects, and patch-specific details.

Decision: fidelity beats raw speed for the first benchmark. The throughput bottleneck is LLM inference, usually seconds per decision, not the game loop. A clone's raw-speed advantage buys little per game, while the real game is ground truth for every card, relic, event, RNG interaction, and patch detail. Training on a buggy clone would bake rule errors into the data and model weights. Win throughput through parallel game instances and batched inference, not by trusting an unverified clone. Keep the `Env` interface backend-agnostic so a simulator can be added later and promoted only after it is validated against the real game.

### 2. What public repos matter?

| Repo / project | Role |
| --- | --- |
| `CommunicationMod` | Foundation for game-process protocol. |
| `spirecomm` | Python package wrapping CommunicationMod, parsing game JSON, modeling actions, and shipping an example AI. Best starting point for the environment layer. |
| `Bottled AI` | Maintained CommunicationMod bot; useful reference for action handling, strategy, tests, and bot structure. |
| `decapitate-the-spire` | Python headless clone attempt; useful reference and possible CI backend, but WIP/partial content and not primary training source. |
| `sts_lightspeed` | Fast C++ simulator/tree-search project; useful later for rollouts, search, or oracle tools. |
| `MiniSTS` | Simplified Slay the Spire environment and prior art on language-driven play and state representation. |
| `GamingAgent` / Orak | Benchmark-design reference. Orak benchmarks LLM agents across games including Slay the Spire with reflection -> planning -> action loops, memory, MCP-style plug-and-play interfaces, normalized scoring, human-novice baselines, and expert-trajectory fine-tuning. |
| Inspect AI | Optional outer eval framework pattern: dataset + solver/agent + scorer + logging. |
| `lm-evaluation-harness` | Excellent for static NLP benchmarks, less natural for this long-horizon game environment. |

Sources:

- https://github.com/ForgottenArbiter/CommunicationMod
- https://github.com/ForgottenArbiter/spirecomm
- https://github.com/xaved88/bottled_ai
- https://github.com/jahabrewer/decapitate-the-spire
- https://deepwiki.com/gamerpuppy/sts_lightspeed
- https://github.com/iambb5445/MiniSTS
- https://dl.acm.org/doi/fullHtml/10.1145/3649921.3650013
- https://github.com/lmgame-org/GamingAgent
- https://arxiv.org/html/2506.03610v2
- https://inspect.aisi.org.uk/
- https://github.com/EleutherAI/lm-evaluation-harness

### 3. What should the middleware layer look like?

Use four clean layers:

```text
Benchmark Runner / Harness
  seeds, N runs, async parallelism, logging, scoring

Agent Scaffold
  prompt construction, memory, reflection/planning, action parsing

Model Provider Interface
  OpenAI-compatible provider; configurable base_url/model

Environment / Game Interface
  spirecomm + CommunicationMod now; optional simulator later
```

The model provider should not know about Slay the Spire. The environment should not know about OpenAI, Anthropic, vLLM, or any model vendor. The agent scaffold is the bridge: it serializes state, calls the provider, receives a structured tool call, and asks the validator to map it to a legal game command. Keep the scaffold swappable, for example zero-shot versus reflection/planning, so model contribution and scaffold contribution can be measured separately.

### 4. Do models need tools?

Yes. Tools define both the action surface and the observability boundary; they should not secretly play the game.

Tools fall into three classes with different rules:

**Action tools** — the only way to affect the game. Always present:

- `play_card(card_index, target_index | null)`
- `end_turn()`
- `choose(option_index)`
- `use_potion(slot_index, target_index | null)`
- `discard_potion(slot_index)`
- `proceed()`
- `return_back()`

**Observation tools** — first-class, not an ablation knob. The default scaffold sends a compact, cursory state view (floor, HP, gold, screen, energy, hand, enemy intents) and the model queries for the rest on demand. Each tool returns facts the player could legally see in the real game — never hidden information like future RNG, unrevealed map nodes, or shuffled draw order:

- `get_legal_actions()`
- `get_deck()` / `get_draw_pile()` (unordered, as in-game) / `get_discard_pile()` / `get_exhaust_pile()`
- `get_map()` — floor-by-floor adjacency from the current position (`floor 3: M(x=1) -> ?(x=0), E(x=2)`), not raw node JSON and not ASCII art; models handle adjacency lists better than either.
- `get_relics()` / `get_potions()`
- `inspect_card(card_id | card_name)` — full card text, including the upgraded variant
- `inspect_relic(relic_id | relic_name)` — full relic text
- `inspect_potion(potion_id | potion_name)` — full potion text

The `inspect_*` text comes from the game's own localization files (`cards.json`, `relics.json`, `potions.json` inside the installed game's `desktop-1.0.jar`), extracted once into a versioned data file. That guarantees exact in-game wording matched to the installed patch — no wiki scraping, no community-dump drift. The payload's `id` field is the join key.

This makes information-gathering itself a measured skill: which facts a model bothers to look up, and at what token cost, is part of the benchmark. Log every observation call.

**Advisor tools** — experimental knobs only. These offload arithmetic, summarization, or search; they must not grant new hidden information:

- `summarize_combat()`
- `evaluate_map_paths()` — enumerate the distinct routes from the current node as symbol sequences (`M ? E R $ ... B`), deduped; this is path *planning* pre-chewed, which is why it is an advisor, not an observation
- `summarize_deck()`
- `simulate_turn()` or `simulate_action()` only when explicitly testing search-augmented agents.

Tool ablations should be part of the benchmark:

```text
LLM only, structured action output, full state in prompt
LLM + cursory state + observation tools (default)
LLM + advisor tools
Reflection/planning scaffold + tools
Hybrid heuristic navigation + LLM combat
```

### 5. One action per call, or plan the whole turn?

Both, as an explicit scaffold axis.

- **Stepwise (default):** the model commits one action per decision point and sees the resulting state before the next. Correct by construction — card draws, enemy deaths, and random effects are observed before the next choice.
- **Turn-script:** the model emits an ordered list of card plays (optionally ending with `end_turn`) and the harness executes them sequentially, re-validating each against the live state. On any invalidation — a queued play becomes illegal, a target died, energy ran out — execution halts and control returns to the model with the failure and the new state. This matches "choose what cards to play in what order, then the game executes," costs far fewer tokens per turn, and directly measures planning quality (script completion rate is a metric).

Stepwise is the baseline because it is unambiguous; turn-script is the interesting comparison.

## CommunicationMod Protocol Notes

CommunicationMod talks to the external process over newline-delimited messages.

Typical flow:

1. Mod launches the Python process.
2. Python process prints `ready` before the startup timeout, or the mod kills it.
3. Mod sends game state JSON when the game is stable.
4. Python process sends a command.
5. Mod executes it and returns the next stable state or an error shaped like `{"error": "...", "ready_for_command": true}`.

The mod captures the external process's stdout and stderr, which is useful for debugging.

Known serializer gaps (from reading `GameStateConverter.java`): the combat player object has **no stance field**, so Watcher's stance is invisible to the harness. Ironclad/Silent/Defect are unaffected (orbs are serialized). Before benchmarking Watcher, either patch the mod (small Java change) or accept stance-blind play and say so in results.

Setup note: copy `CommunicationMod.jar` into the ModTheSpire mods directory, enable it, and set the launch command in the SpireConfig file, for example `command=python /path/to/sts-bench/main.py`.

Important commands include:

| Command | Effect |
| --- | --- |
| `START` | New game with class, ascension, and optional seed. |
| `PLAY` | Play selected card, with target if needed. |
| `END` | End turn. |
| `POTION` | Use or discard a potion. |
| `CHOOSE` | Make a choice on the current screen. |
| `PROCEED` | Click the right-side advance button. |
| `RETURN` | Click the left-side back button. |
| `KEY` | Press mapped game keys such as Confirm, Cancel, Map, Deck, or directions. |
| `CLICK` | Mouse click at `(x, y)`. Keep this out of the normal LLM action surface. |
| `WAIT` | Wait frames or until state changes. |
| `STATE` | Request current state JSON. |

State JSON includes:

- `available_commands`: the current legal-action source of truth.
- `ready_for_command`: whether the mod is ready for the next command.
- `game_state.screen_type` and `game_state.screen_state`.
- combat data: hand, draw pile, discard pile, exhaust pile, monsters, player status.
- run data: class, ascension, floor, gold, potions.
- deck, relics, map layout, and progression.

The `Env` layer must hide the real-time details: animations, waiting, screen transitions, retries, and the `WAIT`/`STATE` loop. Agents should only see stable decision points.

### Process model: the relay pattern

CommunicationMod inverts control: the *mod* launches the external process. That fights a harness that wants to own the run loop, do async orchestration, and survive game restarts. The fix, already working in this repo, is a thin byte-relay:

```text
game/mod ──stdin/stdout──> relay.py ──TCP socket──> harness (long-lived, your process)
```

The mod's `command=` points at `relay.py`, which forwards protocol bytes unmodified to a socket where the harness listens. The harness runs in a normal terminal/process: debuggable, restartable, and — critically — one harness server can accept N relay connections from N game instances, which is exactly the shape the async runner needs. `console.py` (manual command REPL + state digest) is the interactive debugging face of the same pattern and stays useful for protocol exploration.

### Status

- Done: CommunicationMod + spirecomm integration proven end to end (`main.py` runs spirecomm's `SimpleAgent` through full runs); relay/console socket tooling for interactive protocol driving (`relay.py`, `console.py`); protocol logging to `logs/latest.log`.
- Next: the `Env` layer (build-order steps 3+), per `docs/plan.md`.

## Architecture

Proposed package layout:

```text
sts-bench/
  pyproject.toml
  src/sts_bench/
    env/
      base.py                  # Env protocol: reset/step/legal_actions
      communication_mod.py     # spirecomm-backed real-game env
      replay.py                # replay raw traces
      simulator.py             # optional headless backend adapter
      rewards.py               # reward extraction from state deltas
    state/
      schema.py                # Pydantic models for game state
      serialize.py             # compact prompt views
      normalize.py             # stable naming/ids/token cleanup
    actions/
      schema.py                # typed actions/tool calls
      validate.py              # legal-action validation
      translate.py             # Action -> CommunicationMod command
    agents/
      base.py                  # Agent protocol
      zero_shot.py             # stateless: fresh conversation per decision (ablation baseline)
      floor.py                 # default: one conversation per floor (see Context Boundaries)
      reflection_planning.py
      heuristic.py             # baseline
      scripted.py              # smoke-test agent
    providers/
      base.py
      openai_compat.py         # OpenAI/vLLM/SGLang/Ollama/Together/etc.
    tools/
      registry.py
      schemas.py
      card_db.py
      relic_db.py
      pathing.py
      combat.py
    runner/
      async_runner.py
      seeds.py
      metrics.py
      reports.py
    trajectory/
      schema.py                # training-ready trajectory records
      jsonl.py
      export_sft.py
      export_preference.py
```

Core interfaces:

```python
class Env:
    def reset(self, seed: str, character: str, ascension: int) -> GameState: ...
    def legal_actions(self, state: GameState) -> list[Action]: ...
    def step(self, action: Action) -> StepResult: ...


class Agent:
    def choose_action(self, state: GameState, tools: ToolRegistry) -> Action: ...


class ModelProvider:
    def complete(self, messages, tools=None, response_schema=None) -> ModelResponse: ...
```

The agent should never send arbitrary raw commands to the game. It proposes a typed action. The validator accepts/rejects that action against the current state's legal commands. The translator converts accepted actions into CommunicationMod commands.

## Context Boundaries: floors as conversations

The unit of context is the **floor**, not the decision. The game loop divides naturally into floors with outcomes at the boundaries; what matters to a decision is what happened earlier *this* floor (previous combat turns, earlier branches of a multi-part event, the shop already visited), not the tail of the previous fight.

The default agent therefore keeps **one conversation per floor**: each decision appends to the same message list — a tool result closing the previous action call (its fate: executed, or rejected with the reason / a fallback played), then a fresh state digest, then the model's rounds. On floor change the conversation resets to system prompt + digest, plus one carried-over line summarizing the previous floor (cheap insurance against map↔return loops). Consequences:

- **The packet property:** the floor's final request + final response is the complete, self-contained record of the floor. Trajectory storage and analysis lean on this.
- **The model learns its action's fate** — rejections and forced fallbacks land in-conversation, not just in logs.
- **Caching pays:** within a floor each request extends the previous one, the shape provider caches (and the Responses API's carried reasoning) are built for.
- **Token growth is real and bounded by floor length** — tracked per decision; the floor summary reports the floor's total.
- **Log/store each message exactly once, at the level that owns it:** model-traffic logging emits per-decision *deltas* of the conversation; the trajectory's floor record owns the full conversation, decision records hold indices into it. Anything else goes O(n²) per floor.

The stateless zero-shot agent (fresh conversation per decision, `<recent_decisions>` trail) is kept behind a flag as the M6 ablation baseline: stateless vs floor-stateful, same model, same seeds.

## Trajectory Logging

Trajectory logging is a first-class output, not an afterthought. Records form a hierarchy mirroring the context design — **run > floor > decision** — with each piece of data stored once, at the level that owns it:

**Run record** (one per run): run_id, seed, character, ascension, game_version, mod_version, model, provider_base_url, api (wire format), reasoning_effort, agent_scaffold, prompt_hash, tool_schema_hash, outcome (win/loss, floor reached, score), totals (decisions, forced, unparsed, tokens, cost_estimate).

**Floor record** (one per floor; the packet): run_id, floor, floor_type, the **full conversation** (final request messages + final response), entry/exit state summaries, raw_state_json at boundaries, and the floor scorecard — HP delta, gold delta, cards/relics/potions gained, turns taken, reward (versioned `reward_spec_version`).

**Decision record** (one per decision point): run_id, floor, step_id, message index range into the floor conversation (not a copy), available_actions, tool calls made, validation_result(s), command_sent, forced_reason, latency_ms, token_usage (incl. reasoning split and cache reads).

This is both benchmark evidence and future training data. Keep it convertible to:

- chat-message SFT format
- preference format for good/bad action comparisons
- replay/audit format for debugging

## Evaluation Design

Track game outcomes and agent-quality metrics.

Run-level metrics (the headline eval):

- win/loss
- act reached
- floor reached
- boss kills
- final HP
- score
- run duration
- total tokens and cost

Floor-level metrics (the comparison unit — decisions have no observable outcome; floors have a scorecard, and fixed seeds make floors *paired* across models, same encounter same position):

- HP delta vs entry state (entry-conditioned: floor scores are comparative, not absolute)
- combat turns taken
- gold delta; shop value extracted
- reward choices (card taken/skipped, potion use)
- tokens and latency per floor

Decision-level metrics (diagnostics, not eval):

- invalid action rate
- correction retries per decision
- tool-call count, split action vs observation vs advisor
- observation efficiency: which facts were queried before acting, at what token cost
- turn-script completion rate (turn-script scaffold only)
- latency
- repeated/no-op actions
- state serialization token count

Strategic metrics:

- card-pick agreement against baselines or expert traces
- path risk and reward profile
- potion/relic usage
- avoidable damage estimates where available

Seed suites:

```text
smoke:      5 fixed seeds, Ironclad A0
dev:        25 fixed seeds, Ironclad/Silent A0
benchmark: 100+ fixed seeds, all characters, A0/A10/A20
```

Baselines:

- random legal-action baseline
- scripted smoke-test baseline
- `spirecomm` rule-based/example AI where applicable
- human-novice or published reference scores if available

Do not compare models from one run. Compare model/scaffold/tool configurations across fixed seed suites.

## Training Loop

The longer-term loop is:

```text
collect LLM trajectories
export SFT/preference data
fine-tune a smaller open model
serve it behind OpenAI-compatible API via vLLM/SGLang/Ollama/etc.
benchmark it with the same harness
repeat
```

This is why the OpenAI-compatible provider is the right first adapter. A teacher model and a fine-tuned student differ only by `base_url`, `model`, and credentials.

Reward extraction belongs in the environment layer, not the agent. Basic reward signals:

- legal action accepted
- illegal action rejected
- floor advanced
- combat won
- boss killed
- run won
- HP preserved/lost
- card/relic/potion gained

Keep these reward signals explicit and versioned. They are useful for analysis, preference data, and possible RL later.

### RLVR (next iteration)

The planned follow-on is an RL environment with verifiable rewards: the same `Env`, exposed gym-style, where reward comes from game-ground-truth events rather than a judge. Decisions made now that keep that cheap later:

- Rewards live in the env layer, computed from state deltas, and are versioned — an RLVR run can cite exactly which reward spec it trained against.
- `Env.reset/step/legal_actions` is already the RL interface shape; the LLM scaffold sits above it, so an RL training loop can drive `Env` directly.
- Throughput will matter much more for RL than for benchmarking. That is when the headless simulator (`sts_lightspeed` or a validated clone) gets promoted — after validation against real-game trajectories collected by this harness.
- Trajectory logs double as offline RL / preference data, so the logging schema should never drop the raw state needed to recompute rewards under a new reward spec.

## Concurrency

Concurrency is a requirement once the single-game loop works.

Real-game parallelism means multiple JVM/game processes, each with its own CommunicationMod instance. This is memory-heavy, so load-test early. The main scaling strategy is:

- run N game instances concurrently
- send model requests through one provider interface
- use batched inference when the serving backend supports it
- keep every run independently seeded and logged

The practical ceiling on parallelism may be local memory, not Python.

## Build Order

1. ~~Install/run one real game through CommunicationMod and `spirecomm`.~~ Done.
2. ~~Confirm an example/scripted AI can make decisions in a single combat.~~ Done (`main.py` + SimpleAgent).
3. Build `Env.reset`, `Env.step`, `Env.legal_actions`, and transition handling over the relay socket.
4. Add Pydantic state/action/trajectory schemas.
5. Build compact state serialization for prompts.
6. Implement typed action tools, validator, and command translator.
7. Add `OpenAICompatProvider`.
8. Win or complete one combat with an LLM through tool-calling.
9. Add JSONL trajectory logging in training-ready chat format.
10. Add async runner for a fixed seed suite.
11. Add baselines and reports.
12. Add reflection/planning scaffold and tool ablations.
13. Export trajectories for SFT/preference training.
14. Serve a fine-tuned/open model behind an OpenAI-compatible endpoint and re-benchmark.

## MVP

The first useful milestone:

1. One real Slay the Spire process controlled by Python.
2. Stable state JSON parsed into Pydantic models.
3. Legal action validation.
4. One scripted baseline.
5. One LLM agent using structured tool calls.
6. Five fixed Ironclad A0 seeds.
7. Full JSONL traces.
8. Simple report: floor reached, win/loss, invalid actions, latency, tokens, cost.

## Major Risks

- **Parallel real-game instances are heavy.** Load-test memory and startup overhead before designing large experiments.
- **State representation quality will dominate results.** Compact, stable serialization is a core research artifact.
- **Action validation must be strict.** Invalid actions should produce corrective feedback and be logged.
- **Simulator drift can poison data.** Do not train on a clone until it is validated against the real game.
- **Scaffold effects can hide model differences.** Benchmark model, prompt, tool, and scaffold configurations separately.
