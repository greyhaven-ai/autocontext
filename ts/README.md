# autoctx — TypeScript package

`autoctx` is the Node/TypeScript package for autocontext. It ships the operator-facing CLI, TUI, simulations, investigations, analysis, missions, MCP server, runtime/session primitives, production-trace SDK, and experimental agent-handler surface.

Use the Python package when you need the full Python control plane or local MLX/CUDA training implementation. Use this package when you need Node, npm, the TUI, Fetch/agent adapters, or TypeScript library APIs.

## Install

```bash
bun add -g autoctx
# or
npm install -g autoctx
```

Important: use `autoctx`, not `autocontext`. `autocontext` on npm is a different package and not this project.

From a checkout:

```bash
cd ts
npm install
npm run build
```

## Quick start

```bash
AUTOCONTEXT_AGENT_PROVIDER=deterministic autoctx solve "improve customer-support replies" --iterations 3
```

Use a real provider by setting `AUTOCONTEXT_AGENT_PROVIDER` and its credential:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic ANTHROPIC_API_KEY=... autoctx solve "..." --iterations 3
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8000/v1 AUTOCONTEXT_AGENT_API_KEY=... autoctx solve "..." --iterations 3
AUTOCONTEXT_AGENT_PROVIDER=pi AUTOCONTEXT_PI_COMMAND=pi autoctx solve "..." --iterations 3
```

`ANTHROPIC_API_KEY` is the preferred Anthropic credential env var; `AUTOCONTEXT_ANTHROPIC_API_KEY` remains supported as a compatibility alias.

Supported providers: `anthropic`, `openai`, `openai-compatible`, `gemini`, `mistral`, `groq`, `openrouter`, `azure-openai`, `ollama`, `vllm`, `hermes`, `claude-cli`, `codex`, `pi`, `pi-rpc`, `deterministic`.

Provider routing details live in [../autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md).

## CLI surfaces

| Command                                                | Purpose                                                      |
| ------------------------------------------------------ | ------------------------------------------------------------ |
| `autoctx solve "..." --iterations 3`                   | Generate and run a scenario from a plain-language goal       |
| `autoctx run --scenario <name> --iterations 3`         | Run a saved scenario                                         |
| `autoctx simulate -d "..."`                            | Build/replay/compare simulations                             |
| `autoctx investigate -d "..."`                         | Evidence-driven diagnosis                                    |
| `autoctx analyze --id <id> --type <kind>`              | Inspect runs, simulations, investigations, or missions       |
| `autoctx mission create --name "..." --goal "..."`     | Create verifier-driven goals                                 |
| `autoctx mission run --id <id> --max-iterations 3`     | Execute a mission                                            |
| `autoctx queue add --task-prompt "..." --rubric "..."` | Add evaluation/improvement work                              |
| `autoctx runtime-sessions timeline --run-id <run_id>`  | Inspect provider/tool/child-task timelines                   |
| `autoctx benchmark --scenario <name> --runs 5`         | Run a scenario repeatedly and summarize outcomes             |
| `autoctx export <run_id> --output pkg.json`            | Export a run's playbook/skills as a portable package         |
| `autoctx import-package --file pkg.json`               | Import a portable knowledge package                          |
| `autoctx new-scenario --description "..."`             | Generate a scenario from a plain-language description        |
| `autoctx mcp-serve`                                    | Expose MCP tools                                             |
| `autoctx tui`                                          | Start the terminal UI                                        |
| `autoctx train --scenario <name> --dataset <jsonl>`    | Validate training input and call an injected training runner |
| `autoctx agent run <name> --payload '{...}'`           | Invoke experimental `.autoctx/agents` handlers               |

`train` is a validation/executor-hook surface in TypeScript; end-to-end MLX/CUDA training lives in the Python package unless your application injects a real `TrainingRunner`.

## Python-Only commands

These workflows require infrastructure not shipped in the npm package: `ecosystem`
(multi-provider cycling), `ab-test` (requires the ecosystem runner), `resume` /
`wait` (run recovery infrastructure), and `trigger-distillation` (training
pipeline). They are available via `pip install autocontext`; the npm package's
`train` command is a validation/executor-hook surface only.

## MCP and control plane

```bash
autoctx mcp-serve
```

The MCP server exposes 40+ tools across scenarios, runs, knowledge, evaluation, feedback, solve (`solve_scenario`, `solve_status`, `solve_result`), sandbox (`sandbox_create`, `sandbox_run`, `sandbox_status`, ...), export, and discovery (`capabilities`). Python and TypeScript share the same high-level vocabulary; parity details are tracked in [../docs/scenario-parity-matrix.md](../docs/scenario-parity-matrix.md).

### Interactive run transcript extension

The TypeScript `/ws/interactive` server keeps the base WebSocket
`protocol_version` at `1`. Plain `/ws/interactive` connections retain the exact
legacy v1 hello and run-frame shapes. Clients explicitly opt into durable
transcripts with `/ws/interactive?transcript_protocol_version=1`; that connection
advertises `transcript_protocol_version: 1` plus the `run_transcript_v1` and
`safe_run_stop_v1` capabilities.
Clients may attach a stable `client_run_id` and `command_id` to `start_run`,
operator-control, and chat commands. Run-scoped responses then include stable
`event_id`, monotonic `sequence`, `client_run_id`, and `occurred_at` fields.

Reconnect with:

```json
{
  "type": "resume_run",
  "client_run_id": "control-plane-run-id",
  "after_sequence": 42,
  "command_id": "resume-attempt-id"
}
```

The server replays the exact retained wire frames after that cursor and finishes
with a correlated `ack`. Frames and request fingerprints are synchronously
persisted before side effects or delivery under
`runs/_interactive/run-transcript.ndjson`, survive server restarts, and only store
an allowlisted, size-bounded, redacted presentation payload. Retention is bounded
by age, file bytes, global/per-run frame counts, and command count; command
idempotency has the same finite horizon as its retained request and response.
Compaction uses an fsync-backed atomic replacement. The Python server accepts the
additive metadata fields for schema compatibility but does not advertise this
TypeScript-only retention capability.

To stop the currently bound run, send a retry-stable command:

```json
{
  "type": "stop",
  "client_run_id": "control-plane-run-id",
  "command_id": "stop-attempt-id"
}
```

The TypeScript server synchronously persists and returns a correlated `ack`
before the stop can take effect. Stop is cooperative: a paused run wakes
immediately, while an in-flight provider or scenario operation finishes before
the next safe boundary. Completed generations, artifacts, metrics, and
transcript frames remain available. The terminal receipt is a retained
`run_stopped` event carrying the engine `run_id`, `reason: "operator"`, the
originating `command_id`, `completed_generations`, and optional `best_score`.
Whichever terminal outcome occurs first wins, so a natural completion or failure
cannot later become stopped, and a stop-first run cannot later emit completed or
failed.

Retries with the same command ID never repeat the side effect. Before the
terminal event exists they replay the exact acknowledgement; afterwards they
replay the exact terminal receipt. Cursor replay retains both ordered frames.
This idempotency guarantee shares the transcript's finite retention horizon.
Python validates the additive stop schema for parity but returns a correlated
unsupported-capability error and does not advertise `safe_run_stop_v1`.

## Library usage

```ts
import { createProvider, LLMJudge } from "autoctx";

const provider = createProvider({
  providerType: "anthropic",
  apiKey: process.env.ANTHROPIC_API_KEY ?? "",
});

const judge = new LLMJudge({
  provider,
  model: provider.defaultModel(),
  rubric: "Score clarity and correctness.",
});

const result = await judge.evaluate({
  taskPrompt: "Explain binary search.",
  agentOutput: "Binary search halves the search space each step.",
});
```

Prefer package subpath exports for specialized surfaces:

```ts
import { buildTrace } from "autoctx/production-traces";
import { instrumentClient } from "autoctx/integrations/anthropic";
import type { AutoctxAgentContext } from "autoctx/agent-runtime";
import { connectMcpRuntimeTools } from "autoctx/runtimes/mcp";
```

## Production traces

```ts
import Anthropic from "@anthropic-ai/sdk";
import { FileSink, instrumentClient } from "autoctx/integrations/anthropic";

const sink = new FileSink("./traces/anthropic.jsonl");
const client = instrumentClient(new Anthropic(), {
  sink,
  appId: "billing-bot",
  environmentTag: "prod",
});
```

The SDK captures provider-native content blocks, cache-aware usage, outcome
taxonomy, and dataset/retention helpers. Deeper notes live in
[../docs/analytics.md](../docs/analytics.md),
[../docs/opentelemetry-bridge.md](../docs/opentelemetry-bridge.md), and the
source under `src/production-traces/`.

## Agent handlers

The experimental `autoctx/agent-runtime` subpath discovers handlers only from
`.autoctx/agents` and invokes them with explicit `payload`, `env`, `workspace`,
and `AgentRuntime` capabilities.

```ts
// .autoctx/agents/support.ts
import type { AutoctxAgentContext } from "autoctx/agent-runtime";

export default async function ({ init, payload }: AutoctxAgentContext<{ message: string }>) {
  const runtime = await init();
  const session = await runtime.session("support");
  return session.prompt(payload.message, { role: "support-triager" });
}
```

```bash
autoctx agent run support --payload '{"message":"triage this ticket"}' --json
autoctx agent dev --port 3583
autoctx agent build --target node --out .autoctx/build/node
```

Examples:
[../examples/README.md](../examples/README.md#experimental-typescript-agent-handler).

## Fetch/edge adapters

Generic Fetch/ESM hosts can use static catalogs and explicit host capabilities;
the package does not imply Cloudflare/Vercel/Deno deployment wrappers.

Reference docs moved out of this README:

- [../docs/fetch-api-reference.md](../docs/fetch-api-reference.md)
- [../docs/fetch-host-capability-manifest.md](../docs/fetch-host-capability-manifest.md)
- [../docs/generated-fetch-packaging.md](../docs/generated-fetch-packaging.md)
- [../docs/fetch-conformance.md](../docs/fetch-conformance.md)
- [../docs/fetch-troubleshooting.md](../docs/fetch-troubleshooting.md)
- [../docs/edge-runtime-compatibility.md](../docs/edge-runtime-compatibility.md)

Runnable examples: [`examples/fetch-conformance-host-wrapper.ts`](examples/fetch-conformance-host-wrapper.ts)
(typed executable wrapper) and
[`examples/generated-fetch-runtime-factory-packaging.ts`](examples/generated-fetch-runtime-factory-packaging.ts)
(generic Fetch/ESM packaging).

## Contract probes

```bash
autoctx probes check --suite contract-probes.json
autoctx probes check --suite contract-probes.json --json
autoctx probes extract --trace harness-trace.json --output contract-probes.json
```

Use probes to turn observed harness behavior into strict executable checks.

## Project defaults

`autoctx` searches upward for `.autoctxrc.json`, `.autoctxrc`, or
`autoctx.config.json`. Explicit CLI flags win over config files. Env file
loading for agent handlers is explicit: pass `--env FILE` or set
`AUTOCTX_ENV_FILE` for generated Node servers.

## Development

```bash
npm run build
npm run lint
npm test
```

Keep this README as the package entry point. Put long reference material in
`../docs/`, `examples/`, or source-level API docs.
