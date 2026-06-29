# autocontext Python package

This package is the Python control plane for autocontext: scenario runs, `solve`, simulations, investigations, MCP/HTTP surfaces, persistent knowledge, training-data export, and local training hooks.

Use it when you want the full harness in Python, a CLI installed with `uv`/`pip`, or the MCP/HTTP server that coding agents can call.

## Install

```bash
pip install autocontext
# or, for an isolated CLI tool:
uv tool install autocontext
```

Optional extras:

```bash
pip install 'autocontext[browser]'          # Chrome/CDP capture
pip install 'autocontext[primeintellect]'   # PrimeIntellect sandbox backend
pip install 'autocontext[mcp]'              # MCP server dependencies
```

The CLI entrypoint is `autoctx`. Provider env vars are listed in the repo-level [`.env.example`](../.env.example).

## Run from a checkout

```bash
cd autocontext
uv venv
source .venv/bin/activate
uv sync --group dev

AUTOCONTEXT_AGENT_PROVIDER=deterministic \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

Use a real provider by changing `AUTOCONTEXT_AGENT_PROVIDER` and setting its credential:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic \
ANTHROPIC_API_KEY=... \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

Pi and local CLI providers avoid API-key plumbing when those tools are already authenticated:

```bash
AUTOCONTEXT_AGENT_PROVIDER=pi AUTOCONTEXT_PI_COMMAND=pi uv run autoctx solve "..." --iterations 3
AUTOCONTEXT_AGENT_PROVIDER=claude-cli AUTOCONTEXT_CLAUDE_MODEL=sonnet uv run autoctx solve "..." --iterations 3
AUTOCONTEXT_AGENT_PROVIDER=codex AUTOCONTEXT_CODEX_MODEL=o4-mini uv run autoctx solve "..." --iterations 3
```

## Common commands

| Command                                                                                | Purpose                                                |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `uv run autoctx solve "..." --iterations 3`                                            | Generate and run a scenario from a plain-language goal |
| `uv run autoctx run <scenario> --iterations 3`                                         | Improve an existing scenario                           |
| `uv run autoctx simulate --description "..."`                                          | Create/replay/compare modeled-world simulations        |
| `uv run autoctx investigate --description "..."`                                       | Run synthetic or iterative investigations              |
| `uv run autoctx list` / `status <run_id>` / `show <run_id>`                            | Inspect runs                                           |
| `uv run autoctx replay <run_id> --generation 1`                                        | Replay a generation before accepting knowledge         |
| `uv run autoctx queue add --task-prompt "..." --rubric "..."`                          | Queue evaluation/improvement work                      |
| `uv run autoctx serve --host 127.0.0.1 --port 8000`                                    | Start the local HTTP API                               |
| `uv run autoctx worker --poll-interval 5 --concurrency 2`                              | Process queued tasks beside the API server             |
| `uv run autoctx mcp-serve`                                                             | Expose the MCP tool surface                            |
| `uv run autoctx export-training-data --scenario <name> --all-runs --output data.jsonl` | Build a training corpus                                |
| `uv run autoctx train --scenario <name> --data data.jsonl --time-budget 300`           | Run the local training hook                            |
| `uv run autoctx hermes inspect --json`                                                 | Inspect Hermes Curator state                           |

Saved custom scenarios under `knowledge/_custom_scenarios/` can be rerun and benchmarked by name after their `spec.json` is persisted.

## HTTP, MCP, and agents

```bash
uv sync --group dev --extra mcp
uv run autoctx mcp-serve
```

Python runtime-backed `run` and `solve` calls append provider prompts/responses to run-scoped runtime-session logs. The same logs are readable through the cockpit HTTP API and MCP tools.

Detailed setup moved out of this README:

- External agents and provider routing: [docs/agent-integration.md](docs/agent-integration.md)
- Persistent worker trust boundaries: [docs/persistent-host.md](docs/persistent-host.md)
- Sandbox/executor notes: [docs/sandbox.md](docs/sandbox.md)
- Extension hooks: [docs/extensions.md](docs/extensions.md)

## Contract probes

Contract probes turn observed harness traces into executable checks:

```bash
uv run autoctx probes check --suite contract-probes.json
uv run autoctx probes check --suite contract-probes.json --json
uv run autoctx probes extract --trace harness-trace.json --output contract-probes.json
```

Probe suites are strict JSON: unknown keys fail validation and required observation fields must be present. Pipe stdin with `--suite -` when another tool generates the suite.

## Production traces

Wrap an existing Anthropic/OpenAI client once, then persist emitted traces through a sink:

```python
from anthropic import Anthropic
from autocontext.integrations.anthropic import FileSink, instrument_client

sink = FileSink("./traces/anthropic.jsonl")
client = instrument_client(
    Anthropic(),
    sink=sink,
    app_id="billing-bot",
    environment_tag="prod",
)
```

For lower-level emit APIs, use `autocontext.production_traces.build_trace`
and `write_jsonl`. Architecture notes are in
[../docs/analytics.md](../docs/analytics.md) and
[../docs/opentelemetry-bridge.md](../docs/opentelemetry-bridge.md).

## Training

```bash
uv run autoctx export-training-data \
  --scenario support_triage --all-runs \
  --output training/support_triage.jsonl
uv run autoctx train \
  --scenario support_triage \
  --data training/support_triage.jsonl \
  --time-budget 300
```

For MLX/CUDA setup and case studies, use:

- [docs/mlx-training.md](docs/mlx-training.md)
- [docs/case-study-recursive-loop.md](docs/case-study-recursive-loop.md)
- [case study: on-policy distillation](docs/case-study-on-policy-distillation.md)

## Repository layout

```text
autocontext/
├── src/autocontext/       # Python package
├── tests/                 # pytest suite
├── docs/                  # package-specific docs
├── demo_data/             # small bundled examples
├── migrations/            # SQLite migrations
└── pyproject.toml
```

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Keep this README concise. Add deep reference prose to `docs/` or the repo-level
docs index instead.
