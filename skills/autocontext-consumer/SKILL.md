---
name: autocontext-consumer
description: Use when an agent needs to USE knowledge Autocontext already produced - find which scenarios have knowledge, read the playbook and lessons for one, understand the on-disk file and folder layout, and move knowledge between checkouts. Host-agnostic; requires only the autoctx CLI and the filesystem.
version: 1.0.0
author: Autocontext
license: Apache-2.0
---

# Autocontext: Using Existing Knowledge

## Overview

Autocontext writes what it learns to a knowledge directory. This skill covers
reading and moving that knowledge. To *produce* it, use `autocontext-creator`.

Nothing here assumes a particular agent host, and most of it is plain file
reading - the layout is documented below precisely so an agent can go straight
to the file it wants.

## When to Use

- You want to know whether Autocontext has learned anything about a task.
- You want the current playbook or lessons for a scenario.
- You want to move knowledge from one checkout or machine to another.

Do not use this skill to run scenarios or judge output. That is
`autocontext-creator`.

## Where Knowledge Lives

The root defaults to `./knowledge` and moves with `AUTOCONTEXT_KNOWLEDGE_ROOT`.
Inside it, each scenario owns a directory:

```
<knowledge_root>/
  <scenario>/
    playbook.md              the current approach, rewritten as the loop learns
    lessons.json             accumulated lessons, newest last
    hints.md                 hints carried into the next attempt
    mutation_log.jsonl       one line per change, append-only
    package_metadata.json    present once the scenario has been exported
    reports/<run_id>.md      per-run written reports
  analytics/                 cross-scenario analytics
  _hub/                      shared research hub state
  _evaluator_epochs/         evaluator versioning
```

Directories starting with `_` are shared across scenarios rather than owned by
one. `playbook.md` is the file to read first: it is the current answer, where
`lessons.json` is the history of how it got there.

## Reading Knowledge

The playbook and lessons are plain files. Read them directly:

```bash
cat "${AUTOCONTEXT_KNOWLEDGE_ROOT:-knowledge}/grid_ctf/playbook.md"
```

An absent file means nothing has been learned for that scenario yet. That is a
normal state, not an error.

## Finding Runs

```bash
autoctx list --json
autoctx status "$RUN_ID" --json
autoctx show "$RUN_ID"
```

`list` is the entry point when you do not know what exists.

## Moving Knowledge Between Checkouts

Export a scenario's knowledge as a portable package:

```bash
autoctx export --scenario grid_ctf --output grid_ctf_package.json --json
```

Import one somewhere else. The package file is a positional argument, not a
flag, and it is required:

```bash
autoctx import-package grid_ctf_package.json --json
```

`--conflict` decides what happens when the target scenario already has
knowledge: `overwrite`, `merge`, or `skip`. `--scenario` imports under a
different name than the package was exported from.

Use these rather than copying the directory by hand: the package carries the
metadata that makes the knowledge legible on the far side.

## Reading a Generation in Detail

When a result is surprising, the generation JSON is the level that explains it:

```bash
autoctx replay "$RUN_ID" --generation 1
```

## What Not to Assume

- **Knowledge is scenario-scoped.** A playbook for one scenario says nothing
  about another.
- **`lessons.json` is append-only.** The last entries are the newest; do not
  assume the file is ordered by importance.
- **An empty playbook is meaningful.** It means the loop has not yet found an
  approach worth keeping, which is different from the scenario not existing.
