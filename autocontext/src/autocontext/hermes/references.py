# ruff: noqa: E501
"""Hermes Agent skill reference files for autocontext (AC-702).

Progressive-disclosure docs that live alongside `SKILL.md` so the main
skill stays lean. Each reference answers one specific question a
Hermes agent would have while using autocontext.

Shipping these as Python string constants matches the existing
`hermes/skill.py` pattern (no wheel-data wiring, no resource lookup).
The CLI `autoctx hermes export-skill --with-references` writes them to
disk next to `SKILL.md` so Hermes can load them via the standard
skill-references mechanism.
"""

from __future__ import annotations

# Each reference is a complete markdown document. Order matters for
# `list_references()` output: the curator alignment doc comes first
# because it's the most likely starting point for a Hermes agent.

_HERMES_CURATOR_REFERENCE = """# Hermes Curator + autocontext

Reference for Hermes agents using autocontext alongside Hermes Curator.
Use this when the user asks how the two systems cooperate, or when an
agent needs to decide which side to call for a given operation.

## Headline

- **Hermes Curator is the live skill-library maintainer.**
- **autocontext is the evaluation, trace, replay, export, and local-training layer.**
- autocontext does NOT replace Curator. It observes Curator's outputs,
  evaluates them, and turns them into durable artifacts.

## Who owns what

| Operation                                   | Owner       |
| ------------------------------------------- | ----------- |
| Mutate `~/.hermes/skills/` (add/patch/prune) | Curator     |
| Read-only inspection of Hermes state        | autocontext |
| Run trace / replay / export                 | autocontext |
| Curator decision dataset export             | autocontext |
| Local MLX/CUDA advisor training             | autocontext |
| Apply trained advisor recommendations       | Curator (when the advisor path is proven) |

## Read-only first rule

`autoctx hermes inspect` and `autoctx hermes ingest-curator` and
`autoctx hermes export-dataset` are all **read-only against
`~/.hermes`**. Until the trained-advisor path is shipped and proven
end-to-end, autocontext will not write to Hermes state on its own.
Recommendations from autocontext flow back to Curator as suggestions;
Curator stays the mutation owner.

## What an agent should do

1. Ask the user what they want to learn from Hermes state.
2. Run `autoctx hermes inspect --home ~/.hermes --json` to see what's
   available.
3. If the user wants to analyze curator decisions: `autoctx hermes
   ingest-curator` (traces) or `autoctx hermes export-dataset --kind
   curator-decisions` (training rows).
4. Never propose direct edits to `~/.hermes/skills/` from autocontext.
   Surface findings as evidence and let Curator (or the user) apply
   changes.
"""

_CLI_WORKFLOWS_REFERENCE = """# CLI Workflows

Concrete `autoctx` commands for Hermes terminal usage. Use this when an
agent needs the exact command + flag form for a common workflow.

## Inventory: what does my Hermes home contain?

```bash
autoctx hermes inspect --home ~/.hermes --json
```

Output: JSON summary with `skills`, `bundled_skill_count`,
`hub_skill_count`, `pinned_skill_count`, `archived_skill_count`,
`curator.run_count`, and `curator.latest`. Read-only.

## Install the autocontext skill into Hermes

```bash
autoctx hermes export-skill \
    --output ~/.hermes/skills/autocontext/SKILL.md \
    --json
```

Add `--force` to overwrite. Add `--with-references` (when this
release is on the user's machine) to also write the reference files
described here.

## Ingest curator reports as ProductionTrace JSONL

```bash
autoctx hermes ingest-curator \
    --home ~/.hermes \
    --output traces/hermes-curator.jsonl \
    [--since 2026-05-01T00:00:00Z] \
    [--limit 100] \
    [--json]
```

Privacy defaults: `--include-llm-final` and `--include-tool-args` are
**off by default**. Pass them explicitly if the user wants the LLM
final summary as an assistant message, or raw tool args preserved.

## Export curator decisions as training JSONL

```bash
autoctx hermes export-dataset \
    --kind curator-decisions \
    --home ~/.hermes \
    --output training/hermes-curator-decisions.jsonl \
    [--since 2026-05-01T00:00:00Z] \
    [--limit 1000] \
    [--json]
```

Each row carries strong labels from curator action lists
(`consolidated` / `pruned` / `archived` / `added`), feature-engineered
skill stats, and run-level context. Pinned, bundled, and hub skills
are never mutation targets.

## Evaluate an agent output

```bash
autoctx judge -p "$PROMPT" -o "$OUTPUT" -r "$RUBRIC" --json
```

Or run an improvement loop:

```bash
autoctx improve --scenario my_saved_task -o "$OUTPUT" --json
```

## Inspect a finished run

```bash
autoctx list
autoctx show <run-id>
autoctx replay <run-id> --generation 1
```
"""

_MCP_WORKFLOWS_REFERENCE = """# MCP Workflows

When Hermes already has MCP configured, autocontext is reachable as
MCP tools instead of (or alongside) the CLI. Use this only when MCP is
the simpler path; CLI-first remains the default for visibility and
debuggability.

## Setting up the MCP server

```bash
autoctx mcp-serve
```

The server speaks MCP on stdio. Add it to your Hermes config under
`mcp_servers` (path varies by Hermes deployment):

```jsonc
{
  "mcp_servers": {
    "autocontext": {
      "command": "autoctx",
      "args": ["mcp-serve"]
    }
  }
}
```

## Tool name mapping

Each CLI subcommand maps to an `autocontext_*` MCP tool. Examples:

| CLI command                       | MCP tool                      |
| --------------------------------- | ----------------------------- |
| `autoctx judge`                   | `autocontext_judge`           |
| `autoctx improve`                 | `autocontext_improve`         |
| `autoctx list`                    | `autocontext_list_runs`       |
| `autoctx show <run-id>`           | `autocontext_get_run_status`  |
| `autoctx replay <run-id>`         | `autocontext_run_replay`      |

Full list and argument shapes: `autoctx capabilities --json` enumerates
every available MCP tool with its input schema.

## When to prefer CLI over MCP

- The user wants to see exactly what happened (CLI streams to terminal).
- The operation is one-shot, not part of a workflow loop.
- Hermes is not currently configured for MCP.

## When MCP is the better path

- Hermes is already running and has `mcp_autocontext_*` tools loaded.
- The operation is part of an automated multi-step task.
- The agent needs typed input schemas instead of shell parsing.
"""

_LOCAL_TRAINING_REFERENCE = """# Local Training

How autocontext-exported datasets feed local MLX or CUDA training. Use
this when the user asks "can I train a model from my Hermes data" or
when an agent needs to scope training expectations.

## Scope (read this first)

`autoctx train` produces **narrow advisor classifiers**, not full
agent replacements. The expected use is: should this curator decision
have been made? Should this skill be active vs archived? Was this
consolidation good?

**Small personal Hermes homes will not produce frontier-quality
models.** The size and diversity of the dataset matter more than the
training pipeline. If the user has < 100 curator runs, propose a
shadow-evaluation loop instead of training.

## End-to-end flow

1. Export a labeled dataset:

   ```bash
   autoctx hermes export-dataset \
       --kind curator-decisions \
       --home ~/.hermes \
       --output training/hermes-curator-decisions.jsonl
   ```

2. Inspect the dataset shape:

   ```bash
   head -1 training/hermes-curator-decisions.jsonl | jq .
   ```

   Each row is a flat feature vector + label + confidence. See the
   AC-705 module docstring for the canonical schema.

3. (Future) Train an advisor model:

   ```bash
   autoctx train --backend mlx --dataset training/hermes-curator-decisions.jsonl
   autoctx train --backend cuda --dataset training/hermes-curator-decisions.jsonl
   ```

   The training pipeline adapter for this dataset shape is a follow-up
   (AC-708); for now the dataset shape ships and an external trainer
   can consume the JSONL directly.

4. (Future) Surface advisor predictions back to Hermes Curator as
   **read-only recommendations** (AC-709). Curator stays the mutation
   owner.

## Backend selection

- **MLX**: Apple Silicon laptops with plenty of RAM. Quick iteration.
- **CUDA**: x86 + NVIDIA. Faster wall-clock for the same dataset.

Both backends produce models in the same on-disk format that the
advisor surface (AC-709) will consume.

## What the advisor predicts

Per the AC-708 design, the initial advisor tasks are:

- classify whether a skill is `active` / `stale` / `prunable` /
  `pinned` / `patch-worthy`,
- recommend likely umbrella consolidation targets,
- rank candidate skills for a task/session summary,
- detect low-confidence Curator actions (so an operator can review
  before the decision is durable).

None of these mutate Hermes state. They are evidence + scores;
Curator decides what to do with them.
"""

_REFERENCES: dict[str, str] = {
    "hermes-curator": _HERMES_CURATOR_REFERENCE,
    "cli-workflows": _CLI_WORKFLOWS_REFERENCE,
    "mcp-workflows": _MCP_WORKFLOWS_REFERENCE,
    "local-training": _LOCAL_TRAINING_REFERENCE,
}


def list_references() -> tuple[str, ...]:
    """Return the reference names in canonical order."""

    return tuple(_REFERENCES.keys())


def render_reference(name: str) -> str:
    """Return the markdown body for a single reference.

    Raises ``KeyError`` if the name is not a known reference.
    """

    if name not in _REFERENCES:
        known = ", ".join(_REFERENCES.keys())
        raise KeyError(f"unknown reference {name!r}; known: {known}")
    return _REFERENCES[name].rstrip() + "\n"


__all__ = ["list_references", "render_reference"]
