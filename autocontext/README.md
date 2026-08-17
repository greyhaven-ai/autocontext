# autocontext Python package

This package is the Python control plane for autocontext: scenario runs, `solve`, simulations, investigations, MCP/HTTP surfaces, persistent knowledge, training-data export, and local training hooks.

Use it when you want the full harness in Python, a CLI installed with `uv`/`pip`, or the MCP/HTTP server that coding agents can call.

Generation output that changes prompts or harness behavior is staged as an
immutable `ContextBundle`. It becomes active only after matched screening,
adaptive confirmation, and held-out evaluation; a successful strategy gate by
itself does not activate a context edit. The artifact layout and Python API are
documented in [context bundles](../docs/context-bundles.md).
The `autocontext.analytics.context_attribution` API joins controlled trials to
those immutable digests, plans bounded re-ablation, and returns non-destructive
prompt-selection decisions. See [ablation-backed attribution](../docs/context-attribution.md).

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

Autoresearch checkpoint selection uses a minimum-effect gate by default and
supports adaptive matched-trial confirmation through the Python API. Raw
trials and stopping rationale are persisted separately from deployment
promotion; see [trainer-local statistical confirmation](../docs/training-statistical-confirmation.md).

Long-running campaign operators can opt into a separately routed, frozen
`CampaignAuditor` that reviews a sanitized evidence packet without mutation
authority. Reviews are cached, bounded, and advisory; see the
[read-only campaign auditor](../docs/campaign-auditor.md).

Code and research scenarios can opt into a process-backed `ResearchWorkspace`
with explicit file, import, subprocess, network, and host-bridge grants. The
existing restricted interpreter remains the default; see
[capability-scoped research workspaces](../docs/research-workspaces.md).

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

Ollama uses `llama3.1` by default. Set one local model override to fill every
otherwise-unset role and tier slot, regardless of whether role routing is off
or automatic:

```bash
AUTOCONTEXT_AGENT_PROVIDER=ollama \
AUTOCONTEXT_LOCAL_MODEL=qwen3:32b \
AUTOCONTEXT_PROVIDER_CAPABILITY=frontier \
uv run autoctx solve "..." --iterations 3
```

An explicit `AUTOCONTEXT_MODEL_<ROLE>` or `AUTOCONTEXT_TIER_<TIER>_MODEL`
still takes precedence. `AUTOCONTEXT_LOCAL_MODEL` does not alter Anthropic's
shipped per-role defaults.

With `AUTOCONTEXT_ROLE_ROUTING=auto`, unset OpenAI and OpenAI-compatible tier
slots resolve to GPT-5.6 Sol/Terra/Luna for frontier/mid-tier/fast roles;
OpenRouter resolves the same tiers to Claude Opus/Sonnet/Haiku. A generic
OpenAI-compatible endpoint does not necessarily serve those ids, so set
`AUTOCONTEXT_LOCAL_MODEL` when one gateway model should serve every role.

For endpoint-aware routing and cost estimates, set
`AUTOCONTEXT_PROVIDER_HOSTING` to `local` or `remote` and, for a local
endpoint, set `AUTOCONTEXT_PROVIDER_CAPABILITY` to `fast`, `mid_tier`, or
`frontier`. Empty hosting retains conservative transport inference. A
role-specific endpoint uses the corresponding
`AUTOCONTEXT_<ROLE>_PROVIDER_HOSTING` and
`AUTOCONTEXT_<ROLE>_PROVIDER_CAPABILITY` declarations instead of the default
endpoint's declarations.

OpenAI-compatible role generation requests schema-constrained output by
default. If that changes output quality for a backend, set
`AUTOCONTEXT_CONSTRAINED_OUTPUT=false` to omit schemas from every role request
and use the existing Markdown parsers instead. The setting also applies to
roles with dedicated provider overrides.

Build-time teacher trace collection supports native `deep_think` tool loops on
Anthropic and OpenAI-compatible providers. The collector requires a structured
tool stream by default and keeps it separate from the final answer. Local and
CLI/runtime providers report the capability as unsupported; visible-preamble
fallback is available only through the collector's explicit
`require_thinking_stream=False` option. For GPT 5.6+ models,
`reasoning_effort` selects the external numeric prompt budget while native
reasoning is requested off; a compatible gateway that rejects `none` is clamped
to its lowest advertised level. Thinking payloads may contain sensitive
prompt-derived data and should be redacted before persistence or export.

## Common commands

| Command                                                                                | Purpose                                                                                                |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `uv run autoctx solve "..." --iterations 3`                                            | Generate and run a scenario from a plain-language goal                                                 |
| `uv run autoctx run <scenario> --iterations 3`                                         | Improve an existing scenario                                                                           |
| `uv run autoctx status <run-id> --json` / `watch <run-id> --ndjson`                    | Read one run snapshot or stream snapshots                                                              |
| `uv run autoctx show <run-id> --best --json`                                           | Inspect the best generation                                                                            |
| `uv run autoctx simulate --description "..."`                                          | Create/replay/compare modeled-world simulations                                                        |
| `uv run autoctx investigate --description "..."`                                       | Run synthetic or iterative investigations                                                              |
| `uv run autoctx list` / `status <run_id>` / `show <run_id>`                            | Inspect runs                                                                                           |
| `uv run autoctx replay <run_id> --generation 1`                                        | Replay a generation before accepting knowledge                                                         |
| `uv run autoctx queue add --task-prompt "..." --rubric "..."`                          | Queue evaluation/improvement work                                                                      |
| `uv run autoctx scenario create --family workflow --name support --description "..."`   | Create a reusable scenario through a family-specific pipeline                                            |
| `uv run autoctx serve --host 127.0.0.1 --port 8000`                                    | Start the local HTTP API                                                                               |
| `uv run autoctx worker --poll-interval 5 --concurrency 2`                              | Process queued tasks beside the API server                                                             |
| `uv run autoctx serve mcp`                                                             | Expose the MCP tool surface                                                                            |
| `uv run autoctx export-training-data --scenario <name> --all-runs --output data.jsonl` | Build a training corpus (quarantined scores excluded by default; `--include-quarantined` to keep them) |
| `uv run autoctx train --scenario <name> --data data.jsonl --time-budget 300`           | Run the local training hook                                                                            |
| `uv run autoctx epoch list [--scenario <name>]`                                        | List evaluator-epoch registry records (candidate/active)                                               |
| `uv run autoctx epoch approve <scenario> <epoch_id> --charter ambient-charter.yaml`    | Approve a candidate evaluator epoch and clear its quarantine                                           |
| `uv run autoctx hermes inspect --json`                                                 | Inspect Hermes Curator state                                                                           |

Saved custom scenarios under `knowledge/_custom_scenarios/` can be rerun and benchmarked by name after their `spec.json` is persisted.

## HTTP, MCP, and agents

```bash
uv sync --group dev --extra mcp
uv run autoctx serve mcp
```

Python runtime-backed `run` and `solve` calls append provider prompts/responses to run-scoped runtime-session logs. The same logs are readable through the cockpit HTTP API and MCP tools.

Detailed setup moved out of this README:

- External agents and provider routing: [docs/agent-integration.md](docs/agent-integration.md)
- Self-hosted models, end to end: [docs/self-hosted-models.md](docs/self-hosted-models.md)
- Persistent worker trust boundaries: [docs/persistent-host.md](docs/persistent-host.md)
- Sandbox/executor notes: [docs/sandbox.md](docs/sandbox.md)
- Extension hooks: [docs/extensions.md](docs/extensions.md)
- Correctness-first external kernel evolution: [docs/kernel-evolution.md](docs/kernel-evolution.md)

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
