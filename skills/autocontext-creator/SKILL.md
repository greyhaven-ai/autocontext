---
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
