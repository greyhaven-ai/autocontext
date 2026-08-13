# ruff: noqa: E501
"""AC-925: host-agnostic SKILL.md content, split by what the agent is doing.

Requested externally (GitHub #1251): the shipped `autocontext` skill is written
for Hermes and mixes two jobs. Its description opens "Use when a Hermes agent
needs to...", it devotes sections to Hermes Curator and `~/.hermes` layout, and
it covers producing knowledge and consuming it in one 196-line file.

Two consequences. An agent on any other host reads Hermes instructions it cannot
follow, and an agent that only needs to *read* existing knowledge loads the
whole surface of running scenarios and training to find the four commands it
wants.

So: two skills, neither mentioning a host.

* **creator** — produce knowledge: run scenarios, judge and improve output, and
  see what got written.
* **consumer** — use knowledge someone else produced: find it, read it,
  understand the on-disk layout, and move it between checkouts.

Every command and path below was verified against the shipped CLI and the
storage layer rather than written from memory. Paths come from
`storage/artifacts.py` and `knowledge/`; the command list from `autoctx --help`.
A skill that documents a flag that does not exist is worse than no skill,
because the agent trusts it.
"""

from __future__ import annotations

AUTOCONTEXT_CREATOR_SKILL_NAME = "autocontext-creator"
AUTOCONTEXT_CONSUMER_SKILL_NAME = "autocontext-consumer"


def render_creator_skill() -> str:
    """Return the host-agnostic SKILL.md for producing knowledge."""
    return _CREATOR_SKILL.rstrip() + "\n"


def render_consumer_skill() -> str:
    """Return the host-agnostic SKILL.md for consuming knowledge."""
    return _CONSUMER_SKILL.rstrip() + "\n"


GENERIC_SKILL_RENDERERS = {
    AUTOCONTEXT_CREATOR_SKILL_NAME: render_creator_skill,
    AUTOCONTEXT_CONSUMER_SKILL_NAME: render_consumer_skill,
}


_CREATOR_SKILL = """---
name: autocontext-creator
description: Use when an agent needs to CREATE knowledge with Autocontext - run a scenario or plain-language task through the improvement loop, judge or improve a single output, and inspect what the run produced. Host-agnostic; requires only the autoctx CLI.
version: 1.0.0
author: Autocontext
license: Apache-2.0
---

# Autocontext: Creating Knowledge

## Overview

Autocontext runs an improvement loop over a task and writes what it learned to
disk. This skill covers producing that knowledge. To *read* knowledge that
already exists, use `autocontext-consumer` instead.

Nothing here assumes a particular agent host. The only requirement is that you
can run `autoctx` and read its output.

## When to Use

- You have a task and want Autocontext to improve an approach to it over several generations.
- You have one output and one rubric, and want it scored or improved without a full loop.
- You want to see what a finished run produced.

Do not use this skill to look up existing knowledge. That is `autocontext-consumer`.

## Always Pass `--json` When Parsing

Every command below accepts `--json`. Use it whenever you intend to read the
result programmatically; the human-readable form is not a stable interface.

## Running a Scenario

```bash
autoctx run --scenario grid_ctf --gens 3 --json
```

`--gens` is the number of generations. Each one produces a candidate, scores it,
and folds what it learned into the knowledge for that scenario.

Give the run an id you choose when you need to refer back to it:

```bash
RUN_ID="my_run_$(date +%s)"
autoctx run --scenario grid_ctf --gens 3 --run-id "$RUN_ID" --json
autoctx status "$RUN_ID" --json
```

## Starting From a Plain-Language Task

When there is no scenario, describe the task:

```bash
autoctx solve --description "Improve the support-triage response policy." --gens 3 --json
```

## Scoring or Improving a Single Output

For one-shot work, without a loop:

```bash
autoctx judge --task-prompt "..." --output "..." --rubric "..." --json
autoctx improve --task-prompt "..." --rubric "..." --rounds 3 --json
```

`judge` scores an output you already have. `improve` iterates on it.

## Seeing What a Run Produced

```bash
autoctx list --json
autoctx status "$RUN_ID" --json
autoctx show "$RUN_ID"
autoctx replay "$RUN_ID" --generation 1
```

`show` renders the run's artifacts. `replay` prints the JSON for one generation,
which is the level to inspect when a score looks wrong.

## Watching a Run in Flight

```bash
autoctx watch "$RUN_ID"
```

## Creating a New Scenario

```bash
autoctx new-scenario
```

Scaffolds from the template library. Use this when the task recurs and deserves
a named scenario rather than a one-off `solve`.

## Choosing a Provider

Autocontext defaults to a hosted Anthropic model. To point it somewhere else,
including a local server, set the provider before running:

```bash
export AUTOCONTEXT_AGENT_PROVIDER=openai-compatible
export AUTOCONTEXT_AGENT_BASE_URL=http://localhost:11434/v1
export AUTOCONTEXT_AGENT_API_KEY=no-key
export AUTOCONTEXT_LOCAL_MODEL=llama3.1
autoctx run --scenario grid_ctf --gens 3 --json
```

Keep secrets and base URLs in the environment or the user's profile, never in a
skill file.

## Before a Long Run

`autoctx run` preflights every endpoint it will use and refuses to start on a
dead endpoint, a rejected credential, or a model the server does not serve.
That check is why a misconfigured run fails in seconds rather than after
spending tokens. `--skip-preflight` exists but wastes that protection.

## Privacy

Runs write to the local knowledge root and stay there. Nothing is uploaded.
Treat run artifacts as you would any local file containing the task text and
model output - they contain whatever you put in the prompt.
"""


_CONSUMER_SKILL = """---
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
"""
