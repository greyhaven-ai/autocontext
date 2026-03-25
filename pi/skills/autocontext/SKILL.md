---
name: autocontext
description: >
  Iterative strategy generation and evaluation system. Use when the user wants
  to evaluate agent output quality, run improvement loops, queue tasks for
  background evaluation, check run status, or discover available scenarios.
  Provides LLM-based judging with rubric-driven scoring.
---

# autocontext

autocontext is an iterative strategy generation and evaluation system that uses
LLM-based judging to score and improve agent outputs.

## Available Tools

- **autocontext_judge** - Evaluate agent output against a rubric. Returns a 0-1
  score with reasoning and dimension breakdowns.
- **autocontext_improve** - Run a multi-round improvement loop. The agent output
  is judged, revised based on feedback, and re-evaluated until the quality
  threshold is met or max rounds are exhausted.
- **autocontext_queue** - Enqueue a task for background evaluation by the task
  runner daemon.
- **autocontext_status** - Check the status of runs and queued tasks.
- **autocontext_scenarios** - List available evaluation scenarios and their
  families.

## Quick Start

1. Evaluate output quality:
   Use `autocontext_judge` with a task prompt, agent output, and rubric.

2. Improve output iteratively:
   Use `autocontext_improve` to automatically revise output through
   judge-guided feedback loops.

3. Queue background tasks:
   Use `autocontext_queue` with a spec name to enqueue evaluation tasks.

## Configuration

Set these environment variables:
- `ANTHROPIC_API_KEY` - Required for LLM-based judging
- `AUTOCONTEXT_MODEL` - Model to use (default: claude-sonnet-4-20250514)
- `AUTOCONTEXT_DB_PATH` - SQLite database path (default: ./autocontext.db)

Or create a `.autoctx.json` project config via `autoctx init`.
