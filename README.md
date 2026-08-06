# sts-bench

A benchmark that measures how well LLMs can play Slay the Spire via [CommunicationMod](https://github.com/ForgottenArbiter/CommunicationMod). The agent gets a compact state digest, queries observation tools for detail, and commits to one typed action per decision point.

Results from the benchmark campaign are published at [alavico.github.io/sts-bench-results](https://alavico.github.io/sts-bench-results/) — the campaign table links down to a per-run report with every decision's full conversation.

## Setup

The harness drives the real game through CommunicationMod:

1. Install Slay the Spire with [ModTheSpire](https://github.com/kiooeht/ModTheSpire) and [CommunicationMod](https://github.com/ForgottenArbiter/CommunicationMod).
2. Set the `command` option in CommunicationMod's config to launch the relay, e.g. `command=python3 /path/to/sts-bench/relay.py`. The relay forwards the mod's stdin/stdout to a TCP socket on port 9999; the harness is the server listening there.
3. Keep API keys in a `.env` file in the repo root

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

## Running

Start the harness first, then trigger CommunicationMod's external process in-game:

```bash
uv run python -m sts_bench.play --seed STSBENCH1
```

The default scaffold keeps one conversation per floor, so the model sees its earlier turns on the floor and each action's outcome in context; `--agent stepwise` switches to the stateless per-decision baseline.

## Choosing the backend

`--api` selects the wire format; `--model` the model. Each provider runs through its native API.

| Command | Plays |
|---|---|
| `uv run python -m sts_bench.play` | Claude via the native Messages API (`auto` picks it from the Anthropic key; default `claude-haiku-4-5`) |
| `... --model claude-sonnet-4-6` | Claude native, specific model |
| `... --api responses --model gpt-5.4` | OpenAI via the Responses API (reasoning summaries land in the log) |
| `... --api gemini --model gemini-3.5-pro` | Gemini via the native API (thought summaries and exact thinking-token counts; default `gemini-3.5-flash`) |
| `... --api chat --base-url http://localhost:11434/v1 --model llama3.3` | Any OpenAI-compatible server (Ollama, vLLM, ...) |

`--reasoning-effort <level>` makes the model deliberate and surfaces what reasoning the API exposes (OpenAI: `minimal`–`high`; Claude: `low`–`max`, which also turns on adaptive thinking). Unset uses the provider default, which for gpt-5-class models means almost no reasoning.

Precedence: CLI flags > shell environment > `.env`. `STS_BENCH_API`, `STS_BENCH_MODEL`, `STS_BENCH_BASE_URL`, and `STS_BENCH_API_KEY` pin a default backend in the environment when you'd otherwise retype flags.

For Anthropic Sonnet models, sts-bench applies a local 30,000 input-token-per-minute guard before requests and honors Anthropic 429 retry headers. Set `STS_BENCH_ANTHROPIC_INPUT_TPM` to your org/workspace limit, or `0` to disable the local guard.

## Logs

Logs are grouped one folder per day, `logs/<date>/`: protocol logs at the session root, with `trajectories/`, `reports/`, and `html/` subfolders separating the data, the markdown reports, and the rendered pages. Each run writes `logs/<date>/play-<timestamp>.log` (`logs/<date>/latest.log` symlinks the newest): game wire traffic verbatim (`>>`/`<<`), model traffic (`>m`/`<m`, including reasoning), and a readable narrative (`--`). States the schema can't parse are captured to `logs/unparsed/` — each is a schema gap to fix. `uv run python -m sts_bench.harvest <log>` promotes any structurally new states from a session log into test fixtures.

## Trajectories

Alongside the log, each run writes `logs/<date>/trajectories/play-<timestamp>.jsonl` — the structured record: a run record (config, outcome, totals), one record per floor (the floor's full conversation, raw boundary states, scorecard, versioned reward), and one per decision (indices into the floor conversation plus action, validation counts, latency, tokens). Replay a run from the trajectory alone, no game or model needed:

```bash
uv run python -m sts_bench.replay logs/<date>/trajectories/play-<timestamp>.jsonl
uv run python -m sts_bench.replay <file> --floor 14   # just the floor that went wrong
```

Every replay also checks that each floor's decisions exactly tile the stored conversation.

Resume a stopped run from its trajectory, preserving the original model/API/scaffold config and continuing the same JSONL after a successful replay to the checkpoint:

```bash
uv run python -m sts_bench.resume logs/<date>/trajectories/play-<timestamp>.jsonl
```

`resume` uses the structured trajectory rather than the protocol log: it replays the recorded commands into the live game, backs up the JSONL, removes the old closeout records, restores the current floor conversation, and appends the continuation.

Or render the run as a self-contained HTML report — HP and spend charts across the floors, the route walked on each act map, turn-by-turn tables for every fight, and each decision's full conversation, browsable offline in one file:

```bash
uv run python -m sts_bench.report logs/<date>/trajectories/play-<timestamp>.jsonl   # writes to the session's html/ folder
```

## Queueing runs

`queue` plays a whole list of runs back to back over the same game instance, unattended — each job starts from the main menu with its seed, plays to game over, and dismisses the death screen so the next can begin:

```bash
uv run python -m sts_bench.queue runs.txt
```

The file lists one run per line with the same flags as `sts_bench.play` (blank lines and `#` comments are skipped), so queued jobs need not share anything — model, effort, ascension, and seed all vary per line:

```
--model gpt-5.6-luna --api responses --reasoning-effort medium --seed STSBENCH1
--model gpt-5.6-terra --api responses --reasoning-effort medium --ascension 5 --seed STSBENCH1
--agent random --seed STSBENCH1
```

`--agent random` (uniform over validator-accepted actions) and `--agent scripted` (play the first playable card, otherwise advance) are the free baselines that calibrate the scale: a model has to beat `scripted` before its floor count means strategy. Every job's provider is built — key resolved, backend named — before the first run starts, so a typo on line 7 fails the queue immediately, not an hour in. Each job writes the same protocol log and trajectory a manual `play` session would, and the queue ends with a one-line outcome summary per run; there is no queue-level artifact.

## Reports

Reporting is a separate pass over whatever trajectories you point it at — every trajectory is self-contained, so baselines recorded one day and model runs another combine into one report without replaying anything:

```bash
uv run python -m sts_bench.bench --report-from logs/*/trajectories/*.jsonl --pricing costs.json
```

The report lands in two forms. Markdown goes to `logs/<date>/reports/` and stdout: one row per model/scaffold/effort configuration, a floor-by-seed grid, and a per-run table. The campaign HTML page at `logs/campaign.html` consolidates every run: a sortable configuration table with strategic columns like card-skip rate and potion use, a floor-by-seed heatmap, a floor-reached dot plot, an HP-over-the-run overlay, and spend bars — and every run links down to its single-run HTML report in that session's `html/` folder.

`--pricing costs.json` (model → `[input, output]` dollars per Mtok) adds the cost column. Runs whose prompt or tool schemas differ are never averaged together — the report keeps them in separate rows and says why.

## Development

```bash
uv run pytest
```

The campaign page's assets reload live: serve any set of trajectories and the page re-renders whenever you save an asset:

```bash
uv run python -m sts_bench.report.dev logs/*/trajectories/*.jsonl --pricing costs.json
```

The trajectories are read once; each refresh re-renders from `report/assets/` on disk, so edits to `campaign.js`/`campaign.css`/`campaign.html` show immediately (changes to the Python payload need a server restart). It writes nothing and shares only the render functions with `bench`.
