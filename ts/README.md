# autoctx TypeScript Toolkit

`autoctx` is the Node/TypeScript package in this repo. It is the best entry point if you want a lightweight toolkit for:

- judging agent outputs against rubrics
- running multi-round improvement loops
- running bounded REPL-loop generation and revision sessions
- queueing and polling background tasks
- exposing those workflows over MCP
- embedding the toolkit directly in a TypeScript application

If you want the full multi-generation control plane, dashboard, training loop, scenario runner, and export/import flows, use the Python package in [`../autocontext`](../autocontext/README.md) instead.

## Install

```bash
npm install autoctx
```

From source, run the commands below from the `ts/` directory:

```bash
cd ts
npm install
npm run build
npm run example:repl -- --help
```

## CLI Quick Start

The package ships an `autoctx` CLI with a focused command set:

```bash
npx autoctx judge -p "Write a haiku about testing" -o "Draft output" -r "Score clarity, format, and relevance"
npx autoctx improve -p "Write a haiku about testing" -o "Draft output" -r "Score clarity, format, and relevance" -n 3
npx autoctx repl -p "Write a concise summary of AutoContext." -r "Reward clarity, accuracy, and completeness."
npx autoctx queue -s my-task --priority 1
npx autoctx status
npx autoctx serve
```

`serve` starts the MCP server on stdio.

Development commands can also be run directly through `tsx`:

```bash
npx tsx src/cli/index.ts judge --help
npx tsx src/cli/index.ts improve --help
npx tsx src/cli/index.ts repl --help
npx tsx src/cli/index.ts queue --help
npx tsx src/cli/index.ts status
npx tsx src/cli/index.ts serve
```

## Provider Configuration

The TypeScript package resolves its runtime provider from these environment variables:

- `AUTOCONTEXT_PROVIDER`: `anthropic`, `openai`, `openai-compatible`, `ollama`, or `vllm`
- `AUTOCONTEXT_MODEL`: override the default model name
- `AUTOCONTEXT_BASE_URL`: override the OpenAI-compatible base URL
- `AUTOCONTEXT_API_KEY`: generic API key override
- `ANTHROPIC_API_KEY`: fallback for Anthropic
- `OPENAI_API_KEY`: fallback for OpenAI-compatible providers

Examples:

```bash
ANTHROPIC_API_KEY=... npx autoctx judge -p "Write a haiku" -o "Draft" -r "Evaluate quality"

AUTOCONTEXT_PROVIDER=ollama \
AUTOCONTEXT_MODEL=llama3.1 \
npx autoctx improve -p "Write a haiku" -o "Draft" -r "Evaluate quality"
```

## Which Surface To Use

- `judge`: one-shot scoring of an output against a rubric
- `improve`: multi-round improvement loop with judge feedback and best-output selection
- `repl`: direct REPL-loop session for open-ended draft generation or revision
- `queue`: background task enqueueing for the task runner store
- `serve`: MCP server exposing the same evaluation, improvement, queue, and REPL surfaces

## REPL Surfaces

### Direct CLI REPL

Use `repl` when you want one bounded REPL-loop session and the execution trace that produced it.

```bash
npx tsx src/cli/index.ts repl \
  -p "Write a concise summary of AutoContext." \
  -r "Reward clarity, accuracy, and completeness."
```

Revise an existing draft:

```bash
npx tsx src/cli/index.ts repl \
  -p "Revise the answer to improve clarity." \
  -r "Reward factual accuracy and readability." \
  --phase revise \
  -o "AutoContext is a system that helps agents get better over time."
```

Useful REPL controls:

- `-m, --model`: override the model used for the REPL session
- `-n, --turns`: max REPL turns
- `--max-tokens`: per-turn token cap
- `-t, --temperature`: REPL sampling temperature
- `--max-stdout`: stdout cap per turn
- `--timeout-ms`: code execution timeout
- `--memory-mb`: memory cap for the sandboxed worker

### Improvement Loop With RLM

Use `improve` when you want best-output selection, thresholding, and judge-guided iteration. Add `--rlm` when you want bootstrap generation and revisions to go through the REPL surface.

```bash
npx tsx src/cli/index.ts improve \
  -p "Write a summary of AutoContext." \
  -r "Reward accuracy and clarity." \
  --rlm \
  --rlm-turns 6
```

If you already have a draft, pass it with `-o`. If you omit `-o` and set `--rlm`, the REPL session will generate the initial draft before the improvement loop starts.

## Library Usage

```ts
import { createProvider, LLMJudge, ImprovementLoop, SimpleAgentTask } from "autoctx";

const provider = createProvider({
  providerType: "ollama",
  baseUrl: "http://localhost:11434/v1",
  model: "llama3.1",
});

const judge = new LLMJudge({
  provider,
  model: provider.defaultModel(),
  rubric: "Score clarity, correctness, and usefulness on a 0-1 scale.",
});

const result = await judge.evaluate({
  taskPrompt: "Explain binary search to a new engineer.",
  agentOutput: "Binary search checks the middle element and halves the search space.",
});

const task = new SimpleAgentTask(
  "Explain binary search to a new engineer.",
  "Score clarity, correctness, and usefulness on a 0-1 scale.",
  provider,
  provider.defaultModel(),
);

const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
const improved = await loop.run({ initialOutput: "Binary search is fast.", state: {} });
```

## MCP Tools

`serve` exposes these task-facing MCP tools:

- `evaluate_output`
- `run_improvement_loop`
- `run_repl_session`
- `queue_task`
- `get_queue_status`
- `get_task_result`

Use `run_repl_session` when an external client wants the direct REPL artifact and execution trace. Use `run_improvement_loop` when the client wants judge-gated multi-round improvement and best-output selection.

### Example MCP Client

There is a runnable example client at [examples/run-repl-session.mjs](/Users/jayscambler/.codex/worktrees/86e3/MTS/ts/examples/run-repl-session.mjs).

It spawns the local stdio MCP server, verifies that `run_repl_session` is registered, calls the tool, and prints the parsed JSON payload:

```bash
cd ts
ANTHROPIC_API_KEY=... npm run example:repl
```

Pass custom arguments through `--`:

```bash
cd ts
ANTHROPIC_API_KEY=... npm run example:repl -- \
  --prompt "Write a concise summary of AutoContext." \
  --rubric "Reward clarity, accuracy, and completeness."
```

## Notes

- The current TS REPL runtime is Node-based and uses `secure-exec` for bounded execution.
- This surface is intentionally aligned with the shared task-runtime path, so CLI, queue, and MCP use the same REPL session implementation rather than separate codepaths.
