# AutoContext TypeScript Toolkit

The `ts/` package is the TypeScript-facing operator surface for agent-task evaluation, improvement, MCP serving, and REPL-loop execution.

## Working Directory

Run the commands in this guide from the `ts/` directory.

```bash
cd ts
npm install
npm run example:repl -- --help
```

## Main CLI Commands

Development commands can be run directly through `tsx`:

```bash
npx tsx src/cli/index.ts judge --help
npx tsx src/cli/index.ts improve --help
npx tsx src/cli/index.ts repl --help
npx tsx src/cli/index.ts queue --help
npx tsx src/cli/index.ts status
npx tsx src/cli/index.ts serve
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
