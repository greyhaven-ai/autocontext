---
name: autocontext
description: >
  Iterative strategy generation and evaluation system. Use when the user wants
  to evaluate agent output quality, run improvement loops, queue tasks for
  background evaluation, check run status, or discover available scenarios.
  Provides LLM-based judging with rubric-driven scoring.
allowed-tools: autocontext_judge autocontext_improve autocontext_status autocontext_scenarios autocontext_queue
---

# autocontext

autocontext is an iterative strategy generation and evaluation system that uses
LLM-based judging to score and improve agent outputs.

## Available Tools

- **autocontext_judge** — Evaluate agent output against a rubric. Returns a 0–1
  score with reasoning and per-dimension breakdowns.
- **autocontext_improve** — Run a multi-round improvement loop. The agent output
  is judged, revised based on feedback, and re-evaluated until the quality
  threshold is met or max rounds are exhausted.
- **autocontext_queue** — Enqueue a task for background evaluation by the task
  runner daemon.
- **autocontext_status** — Check the status of runs and queued tasks.
- **autocontext_scenarios** — List available evaluation scenarios and their
  families.

## Quick Start

### 1. Evaluate output quality

Use `autocontext_judge` with a task prompt, the agent's output, and a rubric:

```
autocontext_judge(
  task_prompt="Write a Python function to parse CSV files",
  agent_output="def parse_csv(path): ...",
  rubric="Correctness, error handling, edge cases, documentation"
)
```

### 2. Improve output iteratively

Use `autocontext_improve` to automatically revise output through
judge-guided feedback loops:

```
autocontext_improve(
  task_prompt="Write a Python function to parse CSV files",
  initial_output="def parse_csv(path): ...",
  rubric="Correctness, error handling, edge cases, documentation",
  max_rounds=5,
  quality_threshold=0.85
)
```

### 3. Queue background tasks

Use `autocontext_queue` with a scenario name to enqueue evaluation tasks
for asynchronous processing:

```
autocontext_queue(spec_name="my_scenario")
```

Check results later with `autocontext_status`.

### 4. Discover scenarios

Use `autocontext_scenarios` to see what evaluation scenarios are available:

```
autocontext_scenarios()
autocontext_scenarios(family="agent_task")
```

## Configuration

The extension auto-detects configuration from these sources:

1. **Project config** — `.autoctx.json` in the working directory (created via `autoctx init`)
2. **Environment variables:**
   - `AUTOCONTEXT_AGENT_PROVIDER` or `AUTOCONTEXT_PROVIDER` — Provider type
   - `AUTOCONTEXT_AGENT_API_KEY` or `AUTOCONTEXT_API_KEY` — Provider API key
   - `AUTOCONTEXT_AGENT_DEFAULT_MODEL` or `AUTOCONTEXT_MODEL` — Model override
   - `AUTOCONTEXT_DB_PATH` — SQLite database path override
3. **Pi provider** — Falls back to Pi's configured LLM provider

## CLI Companion

For standalone usage outside Pi, install the `autoctx` CLI:

```bash
npm install -g autoctx
autoctx init
autoctx solve --description "your problem" --gens 5
autoctx simulate --description "your simulation" --runs 3
```
