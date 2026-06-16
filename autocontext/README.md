# autocontext

autocontext is the Python control-plane package for running scenarios, carrying forward validated knowledge, exporting artifacts, and distilling stable behavior into cheaper runtimes over time.

The intended use is to hand the harness a real task in plain language, let it solve or simulate the problem mostly hands-off, and then inspect the resulting traces, reports, playbooks, datasets, and optional distilled model.

## Install

```bash
pip install autocontext
```

The current PyPI release line is `autocontext==0.7.0`.
The PyPI package name is now `autocontext`. The CLI entrypoint remains `autoctx`.

## Working Directory

Run the commands in this README from the `autocontext/` directory. The Python package, CLI entrypoint, tests, and migrations all live here.

## What It Does

- Runs iterative generation loops against game scenarios and agent-task scenarios
- Adds a first-class `simulate` surface for modeled-world exploration, replay, compare, and export
- Persists playbooks, hints, tools, reports, and snapshots across runs
- Supports staged validation, harness synthesis, and harness-aware routing
- Exports training data and runs autoresearch-style local training loops
- Exposes evaluation, validation, artifact, runtime-session, and discovery operations over MCP and HTTP

## Surface Summary

The Python package is the full control-plane surface in this repo. It currently includes:

- generation-loop execution via `autoctx run`
- plain-language simulation via `autoctx simulate`
- plain-language investigation via `autoctx investigate`
- local training workflows via `autoctx export-training-data` and `autoctx train`
- scenario creation and materialization via `autoctx new-scenario`
- Hermes Agent integration helpers via `autoctx hermes inspect` and `autoctx hermes export-skill` (with optional `--with-references` for progressive-disclosure reference files)
- HTTP API and MCP server surfaces via `autoctx serve` and `autoctx mcp-serve`, including runtime-session log and timeline readers for provider-backed runs

Some newer operator-facing surfaces are currently TypeScript-first:

- `autoctx analyze`
- the interactive terminal UI via `npx autoctx tui`

`campaign` currently lives in that same bucket: it has partial TypeScript CLI/API/MCP support, but the Python package does not expose a campaign control-plane workflow yet.

## Quick Start

From the repo root:

```bash
cd autocontext
uv venv
source .venv/bin/activate
uv sync --group dev
```

Use the repo-level `.env.example` as the reference for available `AUTOCONTEXT_*` settings and supported provider-native credential aliases such as `ANTHROPIC_API_KEY`.

`operator-in-the-loop` is a runnable scenario family for escalation and clarification experiments. Use it when you want executable operator-loop simulations, judgment evaluation, and live-agent escalation workflow testing.

Run a deterministic local scenario:

```bash
AUTOCONTEXT_AGENT_PROVIDER=deterministic \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

Run with Anthropic:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic \
ANTHROPIC_API_KEY=... \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

`ANTHROPIC_API_KEY` is the preferred Anthropic credential env var. `AUTOCONTEXT_ANTHROPIC_API_KEY` remains supported as a compatibility alias.

Run with Claude CLI (`claude -p` via a local authenticated Claude Code runtime):

```bash
AUTOCONTEXT_AGENT_PROVIDER=claude-cli \
AUTOCONTEXT_CLAUDE_MODEL=sonnet \
AUTOCONTEXT_CLAUDE_TIMEOUT=300 \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

For longer live prompts, `autoctx solve`, `autoctx judge`, and `autoctx improve` all accept `--timeout <seconds>`. `autoctx solve` also accepts `--generation-time-budget <seconds>` to cap per-generation solve runtime. You can still use provider env vars such as `AUTOCONTEXT_CLAUDE_TIMEOUT`, `AUTOCONTEXT_CLAUDE_MAX_RETRIES`, `AUTOCONTEXT_CLAUDE_MAX_TOTAL_SECONDS`, or `AUTOCONTEXT_PI_TIMEOUT`.

Run with Codex CLI (`codex exec` via a local authenticated Codex runtime):

```bash
AUTOCONTEXT_AGENT_PROVIDER=codex \
AUTOCONTEXT_CODEX_MODEL=o4-mini \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

Run with Pi CLI (local Pi agent runtime):

```bash
AUTOCONTEXT_AGENT_PROVIDER=pi \
AUTOCONTEXT_PI_COMMAND=pi \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

`autoctx simulate` now follows the effective architect-role runtime surface, so `AUTOCONTEXT_ARCHITECT_PROVIDER`, other role-routing overrides, and per-call `--provider <name>` overrides all apply to live simulation generation.

`autoctx investigate` now ships as a first-class Python CLI surface as well. It uses the architect runtime for investigation-spec synthesis and the analyst runtime for hypothesis generation, so role-routing overrides apply there too. The default `--mode synthetic` creates and executes a compact investigation harness. `--mode iterative` runs a live multi-step LLM investigation, emits `events.ndjson` rows, and writes Pi-shaped compaction ledger entries under `runs/<investigation_id>/` when context budget pressure triggers compaction. When browser exploration is enabled, `--browser-url <url>` captures a policy-checked snapshot and folds that evidence into the investigation prompts and report artifacts.

Run with Pi RPC (local Pi subprocess using `pi --mode rpc` JSONL):

```bash
AUTOCONTEXT_AGENT_PROVIDER=pi-rpc \
AUTOCONTEXT_PI_COMMAND=pi \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

For deterministic evals where Pi should ignore repo-local `AGENTS.md` / `CLAUDE.md`, add:

```bash
AUTOCONTEXT_PI_NO_CONTEXT_FILES=true
```

For Pi-shaped harness runs with a tighter prompt budget and exported tool-affordance metadata, add:

```bash
AUTOCONTEXT_HARNESS_PROFILE=lean
AUTOCONTEXT_LEAN_CONTEXT_BUDGET_TOKENS=32000
AUTOCONTEXT_LEAN_TOOL_ALLOWLIST=read,bash,edit,write
AUTOCONTEXT_PI_RPC_PERSISTENT=true
```

Run with Hermes (via OpenAI-compatible gateway):

```bash
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1 \
AUTOCONTEXT_AGENT_API_KEY=no-key \
AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

Start the API server:

```bash
uv run autoctx serve --host 127.0.0.1 --port 8000
```

Inspect `http://127.0.0.1:8000/` for the API index after the server starts. Browser-based GUI clients calling the HTTP API cross-origin are allowed via CORS for local app origins by default (the cowork desktop webview and dev servers); set `AUTOCONTEXT_CORS_ORIGINS` to a comma-separated origin list for remote or custom GUI deployments. For an interactive terminal UI, use the TypeScript package: `npx autoctx tui`.

Run a persistent queue worker beside the API server:

```bash
uv run autoctx worker --poll-interval 5 --concurrency 2
```

Stateful persistent providers, such as persistent Pi RPC, run with effective concurrency `1` so one long-lived runtime cannot mix events across tasks. The persistent-host worker is a single-tenant/trusted-org deployment shape; review [persistent-host trust guidance](docs/persistent-host.md#trust-and-credential-boundary) and the repo-level [background execution trust boundaries](../docs/background-execution-trust-boundaries.md) before exposing it beyond a trusted network or adding SCM/sandbox credentials.

Start the MCP server:

```bash
uv sync --group dev --extra mcp
uv run autoctx mcp-serve
```

Python runtime-backed `run` and `solve` role calls automatically append provider prompts and responses to the run-scoped runtime-session log. Runtime-session logs created by the TypeScript runtime-session provider bundle can be read from Python too when both packages point at the same `AUTOCONTEXT_DB_PATH`. Python command grants mirror the TypeScript runtime grant vocabulary for command lifecycle events: trusted env values stay out of prompt text, local command wrappers inherit only explicitly allowlisted host env, start/end/error payloads redact against the effective grant env, and child tasks inherit only grants whose scope policy allows it. The Python cockpit API exposes `GET /api/cockpit/runtime-sessions`, `GET /api/cockpit/runtime-sessions/{session_id}`, `GET /api/cockpit/runtime-sessions/{session_id}/timeline`, `GET /api/cockpit/runs/{run_id}/runtime-session`, and `GET /api/cockpit/runs/{run_id}/runtime-session/timeline`. The Python MCP server exposes the same read model through `autocontext_list_runtime_sessions`, `autocontext_get_runtime_session`, and `autocontext_get_runtime_session_timeline`, plus unprefixed aliases.

## Main CLI Commands

```bash
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
uv run autoctx simulate --description "simulate deploying a web service with rollback"
uv run autoctx simulate --description "simulate deploying a web service with rollback" --provider claude-cli
uv run autoctx investigate --description "why did conversion drop after Tuesday's release"
uv run autoctx investigate --description "debug the outage timeline" --mode iterative
uv run autoctx investigate --description "checkout is failing in prod" --browser-url https://status.example.com
uv run autoctx queue add --task-prompt "Write a 1-line fact about primes" --rubric "correct" --threshold 0.8 --rounds 2
uv run autoctx queue --spec support_triage --browser-url https://status.example.com
uv run autoctx worker --poll-interval 5 --concurrency 2
uv run autoctx simulate --replay deploy_sim --variables threshold=0.9
uv run autoctx list
uv run autoctx status <run_id>
uv run autoctx show <run_id> --best
uv run autoctx show <run_id> --generation 2 --json
uv run autoctx watch <run_id> --interval 2
uv run autoctx replay <run_id> --generation 1
uv run autoctx run support_triage --iterations 3
uv run autoctx benchmark --scenario support_triage --runs 5
uv run autoctx new-scenario --template prompt-optimization --name support_triage
uv run autoctx export-training-data --scenario support_triage --all-runs --output training/support_triage.jsonl
uv run autoctx train --scenario support_triage --data training/support_triage.jsonl --time-budget 300
uv run autoctx hermes inspect --json
uv run autoctx hermes export-skill --output ~/.hermes/skills/autocontext/SKILL.md --json
uv run autoctx hermes export-skill --output ~/.hermes/skills/autocontext/SKILL.md --with-references --json
uv run autoctx analytics context-selection --run-id <run_id> --json
uv run autoctx analytics trace-findings --trace-id <trace_id> --kind writeup --json
uv run autoctx analytics trace-findings --trace-id <trace_id> --kind weakness
uv run autoctx serve --host 127.0.0.1 --port 8000
uv run autoctx mcp-serve
uv run autoctx probes check --suite contract-probes.json
uv run autoctx probes check --suite contract-probes.json --json
uv run autoctx probes extract --trace harness-trace.json
uv run autoctx probes extract --trace harness-trace.json --output contract-probes.json
uv run autoctx mission create --name "Ship login" --goal "Implement OAuth"
uv run autoctx mission run --id <mission_id> --max-iterations 3 --json
uv run autoctx mission status --id <mission_id> --json
uv run autoctx mission list --status active --json
uv run autoctx mission pause --id <mission_id>
uv run autoctx mission resume --id <mission_id>
uv run autoctx mission cancel --id <mission_id>
uv run autoctx mission artifacts --id <mission_id> --json
uv run autoctx wait <condition_id> --json
```

Saved custom scenarios under `knowledge/_custom_scenarios/` can be rerun and benchmarked by name once their `spec.json` has been persisted, so the `new-scenario` / `solve` workflow lines up with the named `run` and `benchmark` surfaces.

Trace-finding reports read persisted `RunTrace` files from `knowledge/analytics/traces/`.
Use the filename without `.json` as `--trace-id` (for example
`trace-run-123` from `knowledge/analytics/traces/trace-run-123.json`), or run
`uv run autoctx analytics rebuild-traces --run-id <run_id> --json` to rebuild
trace artifacts from an events stream first. `--kind writeup` emits the full
summary/findings/motifs/recovery-path shape; `--kind weakness` emits
recommendations, weakness findings, motifs, and recovery analysis.

Useful variants:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic ANTHROPIC_API_KEY=... \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3

AUTOCONTEXT_AGENT_PROVIDER=anthropic \
ANTHROPIC_API_KEY=sk-ant-primary \
AUTOCONTEXT_COMPETITOR_PROVIDER=openai-compatible \
AUTOCONTEXT_COMPETITOR_API_KEY=sk-role \
AUTOCONTEXT_COMPETITOR_BASE_URL=http://localhost:8000/v1 \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3

AUTOCONTEXT_AGENT_PROVIDER=deterministic AUTOCONTEXT_RLM_ENABLED=true \
uv run autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

## Contract Probes

`uv run autoctx probes check --suite <path>` runs the contract-probe suite against observed harness state and reports per-probe pass/fail. It exits 0 on a full pass and 1 on any failure or any load / parse error. Default output is human-readable; pass `--json` to emit a structured `ContractProbeSuiteResult` payload (`{passed, results: [{kind, label?, passed, failures: [{kind, message, ...}]}]}`) that downstream tools can consume. On load / parse / validation failure, the typer wrapper writes the actionable error to stderr so `--json` consumers can safely parse stdout.

The suite file is a JSON document validated by `ContractProbeSuiteSchema`. Every nested model is strict: unknown keys (for example a typo like `requiredStdoutPattern` missing the trailing `s`) fail validation rather than silently disappearing. Required observation fields (`presentFiles`, `observed`, `entries`, `ranks`) must be present and primitives are not coerced (`"exitCode": "0"` rejects). Optional expectation fields accept omission but not explicit `null`.

Stdin form lets the canonical pipe pattern stay cross-platform:

```bash
uv run autoctx probes check --suite contract-probes.json --json
cat contract-probes.json | uv run autoctx probes check --suite -
```

Minimal suite example:

```json
{
  "schema_version": 1,
  "probes": [
    {
      "kind": "terminal",
      "label": "solve-cli",
      "inputs": {
        "exitCode": 0,
        "stdout": "solution.txt written",
        "stderr": "",
        "requiredStdoutPatterns": ["solution\\.txt"]
      }
    },
    {
      "kind": "directory",
      "label": "final-workdir",
      "inputs": {
        "presentFiles": ["solution.txt"],
        "requiredFiles": ["solution.txt"],
        "allowedFiles": ["solution.txt"]
      }
    }
  ]
}
```

The seven probe kinds (`directory`, `terminal`, `service`, `artifact`, `cleanup`, `media`, `distributed`) each verify a different facet of observed harness state. RegExp values accept a bare pattern string or `{"source": ..., "flags": ...}`; ISO-8601 strings parse to `datetime`. Every declared expectation must carry a corresponding observation; missing observations fail with kind `missing-observation` rather than silently passing.

### Synthesizing a suite from a harness trace

`uv run autoctx probes extract --trace <path>` reads a harness-trace JSON envelope that bundles two halves:

- `observations`: what actually happened in a recorded run (terminal exit code / stdout / stderr; the workdir's present files; observed service endpoints; emitted artifacts and their content).
- `expectations`: what the operator declared should have happened (expected exit code, required / allowed files, required endpoints, per-artifact JSON-field / substring / line-ending expectations).

The extractor joins them into a runnable `ContractProbeSuite` that `autoctx probes check` can execute. Per-artifact expectations join observations by `path`; observations without a matching expectation are still encoded as no-op artifact probes (path + content only, no assertions). Orphan expectations (declared without a matching observation) fail validation at parse time rather than producing a vacuously-passing suite.

Pipe form: `autoctx probes extract --trace t.json | autoctx probes check --suite -` works cross-platform; the typer wrapper writes parse / validation errors to stderr so JSON consumers can parse stdout cleanly. `--output <path>` writes the suite to a file (parent directories are created); omit it to emit to stdout.

Slice 5 covers the four base probe kinds (`terminal`, `directory`, `service`, `artifact`); cleanup / media / distributed extraction lands in a follow-up slice. Minimal trace example:

```json
{
  "schema_version": 1,
  "label": "solve-cli",
  "observations": {
    "terminal": { "exitCode": 0, "stdout": "solution.txt written", "stderr": "" },
    "workdir": { "presentFiles": ["solution.txt"] },
    "artifacts": [{ "path": "solution.txt", "content": "answer\n" }]
  },
  "expectations": {
    "terminal": { "requiredStdoutPatterns": ["solution\\.txt"] },
    "directory": { "requiredFiles": ["solution.txt"], "allowedFiles": ["solution.txt"] },
    "artifacts": [{ "path": "solution.txt", "requiredSubstrings": ["answer"] }]
  }
}
```

## Missions

`uv run autoctx mission ...` manages long-running, verifier-driven missions. A mission bundles a goal, a budget, a status that follows the state-machine table (active / paused / blocked / completed / failed / canceled / budget_exhausted / verifier_failed), and an optional verifier that gates completion. Two flavours land:

- Generic missions advance via a subgoal-stepping loop; the fallback verifier passes once every subgoal reaches a terminal state.
- Code missions register a `CodeMissionSpec` whose `test_command` (and optional `lint_command` / `build_command`) is wrapped in a `CommandVerifier` (composite when multiple commands are supplied). A code mission rejected by its verifier is downgraded from `active` to `failed` when the run loop returns.

Every subcommand emits human-readable text by default and a structured JSON payload under `--json`. Errors route to stderr so JSON consumers can safely parse stdout on failure paths. State persists under `settings.db_path`; each `create` / `run` / `pause` / `resume` / `cancel` action writes a fresh checkpoint under `<settings.runs_root>/missions/<mission_id>/checkpoints/`. The checkpoint filename embeds nanosecond resolution + an 8-char uuid suffix so concurrent writes never collide; loaders accept both Python-shaped (snake_case) and TS-shaped (camelCase) checkpoints so a shared `AUTOCONTEXT_DB_PATH` can resume from either runtime.

Minimal end-to-end (generic mission):

```bash
uv run autoctx mission create --name "Ship login" --goal "Implement OAuth" --json
# -> {"id": "mission-abc12345", "status": "active", ...}
uv run autoctx mission run --id mission-abc12345 --max-iterations 3 --json
# -> {"id": "...", "finalStatus": "active", "stepsExecuted": 3, "verifierPassed": false, ...}
uv run autoctx mission status --id mission-abc12345 --json
uv run autoctx mission pause --id mission-abc12345 --json
uv run autoctx mission cancel --id mission-abc12345 --json
uv run autoctx mission artifacts --id mission-abc12345 --json
```

Code mission example:

```bash
uv run autoctx mission create \
  --type code \
  --name "Fix login" \
  --goal "Tests pass" \
  --repo-path . \
  --test-command "pytest -x" \
  --lint-command "ruff check src" \
  --max-steps 5 \
  --json
uv run autoctx mission run --id <mission_id> --max-iterations 5 --json
```

Status transitions are enforced by the slice-2 transition table. Only `completed` is truly terminal (self-loop only). `canceled` can be reopened via `resume` (e.g. an operator who changes their mind), so a `mission cancel` followed by `mission resume` returns the mission to `active`. `paused -> completed` rejects because the verifier must observe a live mission. An invalid transition surfaces a clear error and does not mutate state. Async verifiers and async step executors are supported; the CLI is a sync entry point so they cannot be combined with a running event loop (calling from inside an active loop raises `AsyncContextError` before any state mutation).

## Training Workflow

Export JSONL training data from completed runs:

```bash
uv run autoctx export-training-data \
  --scenario support_triage \
  --all-runs \
  --output training/support_triage.jsonl
```

Launch the autoresearch-style training loop:

```bash
uv sync --group dev --extra mlx
uv run autoctx train \
  --scenario support_triage \
  --data training/support_triage.jsonl \
  --time-budget 300
```

MLX training is host-only. It must run on an Apple Silicon macOS machine with Metal access. It will not run correctly inside a Docker sandbox on macOS.

If you only want to inspect generated training data first, export without training and open the JSONL directly.

For host setup details and OpenClaw automation via a file-based watcher bridge, see [docs/mlx-training.md](docs/mlx-training.md).

## Configuration

Configuration is loaded from `AUTOCONTEXT_*` environment variables in `src/autocontext/config/settings.py`.

Common settings:

- `AUTOCONTEXT_AGENT_PROVIDER`
- `AUTOCONTEXT_EXECUTOR_MODE`
- `AUTOCONTEXT_MODEL_COMPETITOR`
- `AUTOCONTEXT_MATCHES_PER_GENERATION`
- `AUTOCONTEXT_MAX_RETRIES`
- `AUTOCONTEXT_JUDGE_PROVIDER`
- `AUTOCONTEXT_PI_TIMEOUT` (defaults to 300 seconds for Pi-backed live runs)
- `AUTOCONTEXT_HARNESS_PROFILE` (`standard` or `lean`)
- `AUTOCONTEXT_LEAN_CONTEXT_BUDGET_TOKENS`
- `AUTOCONTEXT_LEAN_HIDDEN_CONTEXT_BUDGET_TOKENS`
- `AUTOCONTEXT_LEAN_TOOL_ALLOWLIST`
- `AUTOCONTEXT_PI_RPC_PERSISTENT`
- `AUTOCONTEXT_EXTENSIONS`
- `AUTOCONTEXT_EXTENSION_FAIL_FAST`
- `AUTOCONTEXT_RLM_ENABLED`
- `AUTOCONTEXT_SIMPLICITY_MODE` (`off`, `guide`, or experimental guide-only `enforce`)
- `AUTOCONTEXT_HARNESS_PREFLIGHT_ENABLED`
- `AUTOCONTEXT_STAGED_VALIDATION_ENABLED`
- `AUTOCONTEXT_BROWSER_ENABLED`
- `AUTOCONTEXT_BROWSER_ALLOWED_DOMAINS`
- `AUTOCONTEXT_BROWSER_PROFILE_MODE`
- `AUTOCONTEXT_BROWSER_ALLOW_AUTH`
- `AUTOCONTEXT_BROWSER_ALLOW_DOWNLOADS` and `AUTOCONTEXT_BROWSER_DOWNLOADS_ROOT`

Browser exploration defaults to a secure disabled posture and uses the shared contract described in [../docs/browser-exploration-contract.md](../docs/browser-exploration-contract.md).
The Python package includes a thin Chrome CDP backend that attaches to an existing debugger endpoint, enforces the browser allowlist, and stores browser evidence under run-local roots.

`AUTOCONTEXT_HARNESS_PROFILE=lean` resolves a Pi-shaped runtime profile: prompt context is capped by `AUTOCONTEXT_LEAN_CONTEXT_BUDGET_TOKENS`, hidden/implicit context defaults to zero, and generated tool context is replaced by the lean allowlist before agent execution. `AUTOCONTEXT_PI_RPC_PERSISTENT=true` opts Pi RPC into a long-lived subprocess; one-shot Pi RPC remains the default.

`AUTOCONTEXT_EXTENSIONS` loads comma-separated extension modules that register Pi-shaped runtime hooks for context transforms, provider requests/responses, judge calls, artifact writes, and run/generation lifecycle events. Python runs load Python modules or `.py` files; TypeScript runs load JavaScript/ESM modules. See [docs/extensions.md](docs/extensions.md).

Semantic prompt compactions are also persisted as Pi-shaped JSONL entries at
`runs/<run_id>/compactions.jsonl`, including `summary`, `firstKeptEntryId`,
`tokensBefore`, and component details for runtime snapshots and resumption.

Solved strategy packages can also be exported as Pi-local package directories:

```bash
uv run autoctx export <run_id> --format pi-package --output grid-ctf-pi-package
```

The directory contains `package.json`, a Pi skill, a prompt file, and the original `autocontext.package.json` strategy payload for re-import.

See the repo-level [.env.example](../.env.example) for a working starting point.

## Repository Structure

```text
autocontext/
  src/autocontext/   Python package
  tests/             Pytest suite
  docs/              Package-specific documentation
  migrations/        SQLite migrations
ts/                  TypeScript package
infra/               Docker, Fly.io, bootstrap scripts
```

## Validation and Development

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest
```

If you change protocol messages, regenerate the derived protocol artifacts from the repo root:

```bash
cd ..
uv run --directory autocontext python scripts/generate_protocol.py
```

## OpenClaw / ClawHub

autocontext exposes:

- artifact contracts for harnesses, policies, and distilled models
- REST and MCP operations for evaluate, validate, publish, import, and discover
- ClawHub skill manifests and scenario discovery metadata
- an adapter layer for running OpenClaw agents inside the harness

## OpenAI integration

autocontext ships a zero-configuration OpenAI instrumentation path that
automatically wraps your existing `OpenAI(...)` calls and emits structured
traces to a sink of your choice.

### 1. Register detectors

Create `.autoctx.instrument.config.mjs` at the root of your repo:

```js
// .autoctx.instrument.config.mjs
import { registerDetectorPlugin } from "autoctx/control-plane/instrument";
import { plugin as openaiPythonPlugin } from "autoctx/detectors/openai-python";

registerDetectorPlugin(openaiPythonPlugin);
```

### 2. Run instrument

Preview changes without touching any files:

```bash
autoctx instrument --dry-run
```

Apply changes on a new branch for review:

```bash
autoctx instrument --apply --branch autoctx/instrument
```

### 3. Review the PR

The instrument command opens a branch. Open the PR and review the diff — you
will see your `OpenAI(...)` calls wrapped with `instrument_client(...)`.
Edit the generated TODO comment to point at your `FileSink`:

```python
# Before (generated):
client = instrument_client(OpenAI(), sink=None)  # TODO: pass your TraceSink here

# After (your edit):
from autocontext.integrations.openai import instrument_client, FileSink
sink = FileSink("./traces/openai.jsonl")
client = instrument_client(OpenAI(), sink=sink)
```

Merge the PR.

### 4. Customer code emits traces

Your code is unchanged beyond the wrap. Every `chat.completions.create` call
now emits a JSONL trace line to your sink:

```python
import openai
from autocontext.integrations.openai import instrument_client, FileSink, autocontext_session

sink = FileSink("./traces/openai.jsonl")
client = instrument_client(openai.OpenAI(), sink=sink)

with autocontext_session({"userId": "u_123"}):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello!"}],
    )
    print(response.choices[0].message.content)

sink.close()
```

Emitted trace line (pretty-printed for readability):

```jsonl
{
  "schemaVersion": "1.0",
  "traceId": "...",
  "sessionContext": {
    "userId": "u_123"
  },
  "request": {
    "model": "gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "Hello!"
      }
    ]
  },
  "response": {
    "id": "...",
    "choices": [
      {
        "message": {
          "role": "assistant",
          "content": "Hi! How can I help?"
        },
        "finish_reason": "stop"
      }
    ],
    "usage": {
      "prompt_tokens": 9,
      "completion_tokens": 7,
      "total_tokens": 16
    }
  },
  "durationMs": 342,
  "errorReason": null
}
```

For the TypeScript equivalent, see `ts/src/integrations/openai/STABILITY.md`.

## Anthropic integration

autocontext ships a zero-configuration Anthropic instrumentation path that
automatically wraps your existing `Anthropic(...)` calls and emits structured
traces to a sink of your choice.

### 1. Register detectors

Create `.autoctx.instrument.config.mjs` at the root of your repo:

```js
// .autoctx.instrument.config.mjs
import { registerDetectorPlugin } from "autoctx/control-plane/instrument";
import { plugin as anthropicPythonPlugin } from "autoctx/detectors/anthropic-python";

registerDetectorPlugin(anthropicPythonPlugin);
```

### 2. Run instrument

Preview changes without touching any files:

```bash
autoctx instrument --dry-run
```

Apply changes on a new branch for review:

```bash
autoctx instrument --apply --branch autoctx/instrument
```

### 3. Review the PR

The instrument command opens a branch. Open the PR and review the diff — you
will see your `Anthropic(...)` calls wrapped with `instrument_client(...)`.
Edit the generated TODO comment to point at your `FileSink`:

```python
# Before (generated):
client = instrument_client(Anthropic(), sink=None)  # TODO: pass your TraceSink here

# After (your edit):
from autocontext.integrations.anthropic import instrument_client, FileSink
sink = FileSink("./traces/anthropic.jsonl")
client = instrument_client(Anthropic(), sink=sink)
```

Merge the PR.

### 4. Customer code emits traces

Your code is unchanged beyond the wrap. Every `messages.create` call now emits
a JSONL trace line to your sink:

```python
import anthropic
from autocontext.integrations.anthropic import instrument_client, FileSink, autocontext_session

sink = FileSink("./traces/anthropic.jsonl")
client = instrument_client(anthropic.Anthropic(), sink=sink)

with autocontext_session({"userId": "u_123"}):
    response = client.messages.create(
        model="claude-opus-4-7-20251101",
        max_tokens=256,
        messages=[{"role": "user", "content": "Hello!"}],
    )
    print(response.content[0].text)

sink.close()
```

Emitted trace line (pretty-printed for readability):

```jsonl
{
  "schemaVersion": "1.0",
  "traceId": "...",
  "sessionContext": {
    "userId": "u_123"
  },
  "request": {
    "model": "claude-opus-4-7-20251101",
    "messages": [
      {
        "role": "user",
        "content": "Hello!"
      }
    ]
  },
  "response": {
    "id": "...",
    "content": [
      {
        "type": "text",
        "text": "Hi! How can I help?"
      }
    ],
    "stop_reason": "end_turn",
    "usage": {
      "input_tokens": 9,
      "output_tokens": 7
    }
  },
  "durationMs": 342,
  "errorReason": null
}
```

For the TypeScript equivalent, see `ts/src/integrations/anthropic/STABILITY.md`.

## Additional Docs

- [Canonical concept model](../docs/concept-model.md)
- [Agent integration guide](docs/agent-integration.md) — CLI-first integration for external agents, MCP fallback, JSON output reference
- [Sandbox modes](docs/sandbox.md)
- [Persistent host worker](docs/persistent-host.md)
- [MLX host training](docs/mlx-training.md)
- [Case study: recursive loop closed on local MLX](docs/case-study-recursive-loop.md)
- [TypeScript package guide](../ts/README.md) — `analyze`, mission control, and interactive TUI surfaces
- [Demo data notes](demo_data/README.md)
- [Copy-paste examples](../examples/README.md)
- [Change history](../CHANGELOG.md)
- [Repository overview](../README.md)
