<!-- autocontext-readme-hero:start -->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="autocontext/assets/autocontext-wordmark-dark.svg" />
    <source media="(prefers-color-scheme: light)" srcset="autocontext/assets/autocontext-wordmark.svg" />
    <img src="autocontext/assets/autocontext-wordmark.svg" alt="autocontext logo" width="720" style="max-width: 100%; height: auto;" />
  </picture>
</p>

<p align="center"><strong>a recursive self-improving harness designed to help your agents (and future iterations of those agents) succeed on any task</strong></p>

<p align="center">
  <a href="https://github.com/greyhaven-ai/autocontext/blob/main/LICENSE"><img src="https://img.shields.io/github/license/greyhaven-ai/autocontext" alt="License"></a>
  <a href="https://github.com/greyhaven-ai/autocontext/stargazers"><img src="https://img.shields.io/github/stars/greyhaven-ai/autocontext" alt="GitHub stars"></a>
  <a href="https://github.com/greyhaven-ai/autocontext/commits/main"><img src="https://img.shields.io/github/last-commit/greyhaven-ai/autocontext" alt="Last commit"></a>
  <a href="https://pypi.org/project/autocontext/"><img src="https://img.shields.io/pypi/v/autocontext" alt="PyPI version"></a>
  <a href="https://www.npmjs.com/package/autoctx"><img src="https://img.shields.io/npm/v/autoctx" alt="npm version"></a>
</p>

<!-- autocontext-readme-hero:end -->

autocontext is a harness for agent improvement. Give it a goal, it runs the task against evaluation, keeps the useful lessons, discards dead ends, and leaves traces, reports, playbooks, datasets, and optional local-model training artifacts for the next run.

**Docs:** [autocontext.ai/docs](https://autocontext.ai/docs) · [quickstart](https://autocontext.ai/docs/get-started/quickstart) · [CLI reference](https://autocontext.ai/docs/cli/reference) · [changelog](https://autocontext.ai/docs/changelog)

## Install

| Surface             | Command                               |
| ------------------- | ------------------------------------- |
| Python CLI          | `uv tool install autocontext==0.12.0` |
| Python library/dev  | `uv pip install autocontext==0.12.0`  |
| TypeScript/Node CLI | `bun add -g autoctx@0.12.0`           |
| Pi extension        | `pi install npm:pi-autocontext@0.9.0` |

The PyPI package is `autocontext`; the CLI is `autoctx`. The npm package is `autoctx` (not the unrelated `autocontext` npm package). Provider variables live in [`.env.example`](.env.example).

## 30-Second Run

Pi is the lowest-friction provider because it uses your local agent auth:

```bash
AUTOCONTEXT_AGENT_PROVIDER=pi \
AUTOCONTEXT_PI_COMMAND=pi \
autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

Use `AUTOCONTEXT_AGENT_PROVIDER=anthropic`, `openai-compatible`, `claude-cli`, `codex`, `pi-rpc`, or another provider when you need that runtime. See [agent integration](autocontext/docs/agent-integration.md) for the full matrix.

## Agent Entry Points

- **Pi:** install `pi-autocontext`, then ask Pi to solve, judge, improve, list, or inspect runs through the packaged skill.
- **MCP clients:** run `autoctx mcp-serve` or `bunx autoctx mcp-serve` and expose the tools to Claude Code, Cursor, or another MCP client.
- **Hermes:** export the CLI-first skill with `uv run autoctx hermes export-skill --with-references --json`.

Full setup: [autocontext/docs/agent-integration.md](autocontext/docs/agent-integration.md).

## What A Run Leaves Behind

```text
runs/<run_id>/
├── trace.jsonl
├── generations/<n>/{strategy.json,analysis.md,score.json}
├── report.md
└── artifacts/

knowledge/<scenario>/
├── playbook.md
├── hints.md
└── tools/
```

Everything is filesystem-first: inspect it, diff it, replay it, export it, or feed it into training.

## Core Surfaces

| Surface       | Command                                                 | Use it for                                              |
| ------------- | ------------------------------------------------------- | ------------------------------------------------------- |
| `solve`       | `autoctx solve "..." --iterations 3`                    | Start from a plain-language goal                        |
| `run`         | `autoctx run <scenario> --iterations 3`                 | Improve a saved scenario                                |
| `simulate`    | `autoctx simulate -d "..."`                             | Model/replay/compare system behavior                    |
| `investigate` | `autoctx investigate -d "..."`                          | Evidence-driven diagnosis                               |
| `mission`     | `autoctx mission create --name "..." --goal "..."`      | Verifier-driven multi-step goals                        |
| `train`       | `uv run autoctx train --scenario <name> --data <jsonl>` | Distill stable behavior into a cheaper runtime (Python) |
| `mcp-serve`   | `autoctx mcp-serve`                                     | Give an agent the autocontext tool surface              |

Python owns the full control-plane package; TypeScript owns several operator-facing surfaces, the TUI, and Node runtime adapters. Start with [autocontext/README.md](autocontext/README.md) or [ts/README.md](ts/README.md).

<!-- autocontext-whats-new:start -->
## What's New in 0.12.0

- **Safe active-run stopping** lets TypeScript transcript clients stop running or paused work at cooperative boundaries, retain completed generations and best score, and replay an idempotent durable terminal receipt after reconnect or restart.
- **Durable interactive transcripts** add stable run, command, event, and sequence identity with bounded redacted retention plus exact reconnect backfill across server restarts.
- **Ambient live serving** connects promoted per-role targets to live generation through a shared opt-in serving manifest, closing the resident trainer loop from evaluation to serving.
<!-- autocontext-whats-new:end -->

## Scenario Families

The shipped families cover games, agent tasks, simulations, artifact editing, investigations, workflows, negotiation, schema evolution, tool fragility, operator loops, and coordination. Python and TypeScript share the family vocabulary; see [docs/scenario-parity-matrix.md](docs/scenario-parity-matrix.md) for parity details.

## Package Guides

| Need                                          | Go here                                        |
| --------------------------------------------- | ---------------------------------------------- |
| Python CLI/library, MCP, HTTP, training       | [autocontext/README.md](autocontext/README.md) |
| Node CLI, TUI, missions, Fetch/agent adapters | [ts/README.md](ts/README.md)                   |
| Pi package                                    | [pi/README.md](pi/README.md)                   |
| Copy-paste examples                           | [examples/README.md](examples/README.md)       |
| Concepts and docs index                       | [docs/README.md](docs/README.md)               |
| Contributor setup                             | [CONTRIBUTING.md](CONTRIBUTING.md)             |
| Repo guide for agents                         | [AGENTS.md](AGENTS.md)                         |

## Project Signals

[![npm downloads](https://img.shields.io/npm/dm/autoctx?logo=npm&label=npm%20downloads)](https://www.npmjs.com/package/autoctx)
[![PyPI downloads](https://img.shields.io/pypi/dm/autocontext?logo=pypi&label=PyPI%20downloads)](https://pypi.org/project/autocontext/)

[![Star History Chart](https://api.star-history.com/svg?repos=greyhaven-ai/autocontext&type=Date)](https://www.star-history.com/#greyhaven-ai/autocontext&Date)

## Acknowledgments

Thanks to [George](https://github.com/GeorgeH87) for generously donating the `autocontext` name on PyPI.
