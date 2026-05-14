# ruff: noqa: E501
"""Hermes Agent skill content for Autocontext."""

from __future__ import annotations

AUTOCONTEXT_HERMES_SKILL_NAME = "autocontext"


def render_autocontext_skill() -> str:
    """Return a Hermes-valid SKILL.md that teaches Autocontext usage."""

    return _AUTOCONTEXT_HERMES_SKILL.rstrip() + "\n"


_AUTOCONTEXT_HERMES_SKILL = """---
name: autocontext
description: Use when a Hermes agent needs to evaluate agent behavior, run Autocontext scenarios, inspect Hermes curator state, export reusable knowledge, or prepare local MLX/CUDA training data through the autoctx CLI.
version: 1.0.0
author: Autocontext
license: Apache-2.0
metadata:
  hermes:
    tags: [autocontext, evaluation, traces, cli, curator, mlx, cuda, skills]
    related_skills: [native-mcp, hermes-agent-skill-authoring, axolotl]
---

# Autocontext

## Overview

Autocontext is a control plane for evaluating agent behavior, preserving useful run artifacts, exporting training data, and distilling stable behavior into local runtimes. In Hermes, use this skill when the work calls for measurement, replay, datasets, local MLX/CUDA training, or read-only analysis of Hermes skill curation.

Hermes Curator owns Hermes skill mutation. Autocontext should inspect, evaluate, replay, export, and recommend. Do not use Autocontext as a replacement for Hermes Curator, and do not edit Hermes skills directly unless the user explicitly asks for that operation.

## When to Use

- You need to run an Autocontext scenario from Hermes and inspect the result.
- You need machine-readable status for runs, solved knowledge, or training jobs.
- You need to inspect Hermes v0.12 Curator reports, skill usage counters, pinned state, or skill provenance.
- You need to export Autocontext knowledge into a reusable package or skill-like artifact.
- You need to prepare data for local MLX or CUDA training.
- You need to decide whether MCP is useful in a configured environment.

Do not use this skill for normal Hermes memory updates, direct skill consolidation, or user-local skill deletion. Those are Hermes Curator responsibilities.

## Integration Surface Order

Use the CLI first. The `autoctx` CLI is the default surface because Hermes agents can run it with normal terminal tools, see stdout and stderr, preserve logs, and debug failures without special host configuration.

MCP is optional. Use MCP when the environment already has Autocontext MCP configured and the task benefits from typed schemas, constrained invocation, or tool discovery. Do not require MCP just to wrap a command that the CLI already exposes cleanly.

Use a native Hermes runtime or OpenAI-compatible gateway when Autocontext is calling Hermes as an agent provider. Use a Hermes plugin emitter only when the user specifically needs high-fidelity live traces beyond read-only import of existing Hermes artifacts.

## CLI Quick Start

From a checkout of Autocontext:

```bash
cd autocontext
uv run autoctx --help
```

Inspect Hermes skill and curator state without modifying Hermes:

```bash
uv run autoctx hermes inspect --json
```

For a custom profile or test fixture:

```bash
uv run autoctx hermes inspect --home "$HERMES_HOME" --json
```

Install or refresh this skill into a Hermes profile:

```bash
uv run autoctx hermes export-skill --output ~/.hermes/skills/autocontext/SKILL.md --json
```

If the file already exists and the user wants to replace it:

```bash
uv run autoctx hermes export-skill --output ~/.hermes/skills/autocontext/SKILL.md --force --json
```

## Running Autocontext From Hermes

Use `--json` whenever Hermes needs to parse the result.

```bash
RUN_ID="hermes_$(date +%s)"
uv run autoctx run --scenario grid_ctf --gens 3 --run-id "$RUN_ID" --json
uv run autoctx status "$RUN_ID" --json
uv run autoctx replay "$RUN_ID" --generation 1
```

For a plain-language task:

```bash
uv run autoctx solve --description "Improve the support-triage response policy." --gens 3 --json
```

For one-shot judgment or improvement:

```bash
uv run autoctx judge --task-prompt "..." --output "..." --rubric "..." --json
uv run autoctx improve --task-prompt "..." --rubric "..." --rounds 3 --json
```

## Hermes Runtime Configuration

When Autocontext should call a Hermes-served model through an OpenAI-compatible gateway:

```bash
export AUTOCONTEXT_AGENT_PROVIDER=openai-compatible
export AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1
export AUTOCONTEXT_AGENT_API_KEY=no-key
export AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b
uv run autoctx solve --description "..." --gens 3 --json
```

Keep provider configuration outside the skill when possible. The user or profile should own secrets, base URLs, and model names.

## Working With Hermes Curator

Hermes v0.12 writes Curator reports under `~/.hermes/logs/curator/<timestamp>/run.json` and `REPORT.md`. It tracks skill usage in `~/.hermes/skills/.usage.json`, and protects bundled or hub-installed skills through `.bundled_manifest` and `.hub/lock.json`.

Use:

```bash
uv run autoctx hermes inspect --json
```

Read the output as an inventory:

- `agent_created_skill_count` means Curator-eligible user or agent skills.
- `bundled_skill_count` and `hub_skill_count` are upstream-owned skills and should not be pruned by Autocontext.
- `pinned_skill_count` identifies skills Curator and agents should not modify.
- `curator.latest.counts` summarizes the latest consolidation, pruning, and archive activity.

Autocontext can use these signals for reports, datasets, and recommendations. Hermes Curator remains the writer for Hermes skill lifecycle changes.

## Training Path

For Autocontext-owned runs, export training data and train locally:

```bash
uv run autoctx export-training-data --scenario grid_ctf --all-runs --output training/grid_ctf.jsonl
uv run autoctx train --scenario grid_ctf --data training/grid_ctf.jsonl --backend mlx --time-budget 300 --json
uv run autoctx train --scenario grid_ctf --data training/grid_ctf.jsonl --backend cuda --time-budget 300 --json
```

Use MLX on Apple Silicon hosts. Use CUDA on Linux GPU hosts with a CUDA-enabled PyTorch install. Do not run host-GPU training inside a sandbox unless the user has already provided a host bridge or direct GPU access.

For Hermes Curator artifacts, start with read-only inspection and dataset design before training. Curator reports are decision traces; they are best suited for advisor/ranker/classifier training, not full autonomous skill mutation.

## MCP Workflow When Configured

MCP is optional. If the user has already configured Autocontext MCP, prefer it for structured tool calls that are easier or safer than shell commands. Otherwise, stay with the CLI.

Check the local integration guide before inventing tool names:

```bash
uv run autoctx mcp-serve --help
```

Use MCP only when it adds value beyond the CLI: stable schemas, lower parsing burden, managed tool discovery, or a host policy that disallows shell access.

## Common Pitfalls

1. Treating Autocontext as the Hermes Curator. Autocontext should inspect and recommend; Hermes Curator owns skill mutation.
2. Starting with MCP in an unconfigured environment. Use the CLI first unless MCP is already present and helpful.
3. Mutating `~/.hermes/skills` after inspection. `autoctx hermes inspect` is read-only; keep it that way during analysis.
4. Training on raw curator artifacts without a target. First decide whether the target is ranking, consolidation classification, pruning advice, or model-routing advice.
5. Forgetting `--json` when Hermes needs to parse command output.

## Verification Checklist

- [ ] Use `autoctx hermes inspect --json` before making claims about local Hermes skill state.
- [ ] Confirm pinned skills are not modified.
- [ ] Confirm bundled and hub skills are treated as upstream-owned.
- [ ] Prefer CLI commands for first-run workflows.
- [ ] Use MCP only when configured and materially better for the task.
- [ ] Keep Hermes Curator as the system of record for Hermes skill lifecycle changes.

## References

Progressive-disclosure docs available alongside this skill. Load only when relevant.

- `references/hermes-curator.md` — How Hermes Curator and autocontext cooperate; who owns what; the read-only-first rule.
- `references/cli-workflows.md` — Exact `autoctx` commands for inventory, curator ingest, dataset export, judging, replay.
- `references/mcp-workflows.md` — MCP server setup, CLI-to-MCP tool name mapping, when to prefer MCP over CLI.
- `references/local-training.md` — How autocontext-exported datasets feed local MLX/CUDA advisor training; what the advisor predicts; expected scope.

Operators can write all references next to this skill via `autoctx hermes export-skill --with-references --output <dir>/SKILL.md`.
"""
