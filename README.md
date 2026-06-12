# sts-bench

LLMs play Slay the Spire end to end via [CommunicationMod](https://github.com/ForgottenArbiter/CommunicationMod). The agent gets a compact state digest, queries observation tools for detail, and commits to one typed action per decision point.

## Setup

Keep API keys in a `.env` file in the repo root (gitignored, loaded automatically):

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

`--api` selects the wire format; `--model` the model. Each flagship provider runs through its native API surface — never a compat shim.

| Command | Plays |
|---|---|
| `uv run python -m sts_bench.play` | Claude via the native Messages API (`auto` picks it from the Anthropic key; default `claude-haiku-4-5`) |
| `... --model claude-sonnet-4-6` | Claude native, specific model |
| `... --api responses --model gpt-5.4` | OpenAI via the Responses API (reasoning summaries land in the log) |
| `... --api chat --base-url http://localhost:11434/v1 --model llama3.3` | Any OpenAI-compatible server (Ollama, vLLM, ...) |

`--reasoning-effort <level>` makes the model deliberate and surfaces what reasoning the API exposes (OpenAI: `minimal`–`high`; Claude: `low`–`max`, also enables adaptive thinking). Unset means provider default — for gpt-5-class models, essentially no reasoning.

Precedence: CLI flags > shell environment > `.env`. `STS_BENCH_API`, `STS_BENCH_MODEL`, `STS_BENCH_BASE_URL`, and `STS_BENCH_API_KEY` pin a default backend in the environment when you'd otherwise retype flags.

## Logs

Each run writes `logs/play-<timestamp>.log` (`logs/latest.log` symlinks it): game wire traffic verbatim (`>>`/`<<`), model traffic (`>m`/`<m`, including reasoning), and a readable narrative (`--`). States the schema can't parse are captured to `logs/unparsed/` — they're schema gaps; promote them to fixtures once fixed.

## Trajectories

Alongside the log, each run writes `logs/trajectories/play-<timestamp>.jsonl` — the structured record: a run record (config, outcome, totals), one record per floor (the floor's full conversation, raw boundary states, scorecard, versioned reward), and one per decision (indices into the floor conversation plus action, validation counts, latency, tokens). Replay a run from the trajectory alone, no game or model needed:

```bash
uv run python -m sts_bench.replay logs/trajectories/play-<timestamp>.jsonl
uv run python -m sts_bench.replay <file> --floor 14   # just the floor that went wrong
```

Every replay verifies the packet property: each floor's decisions tile its stored conversation exactly — no message stored twice, none orphaned.

Or render the run as a self-contained HTML report — HP and spend charts across the floors, the route walked on each act map, turn-by-turn tables for every fight, and each decision's full conversation, browsable offline in one file:

```bash
uv run python -m sts_bench.report logs/trajectories/play-<timestamp>.jsonl   # writes the .html next to it
```

## Development

```bash
uv run pytest
```

`docs/plan.md` tracks milestones and design decisions.
