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
| Python CLI          | `uv tool install autocontext==0.15.1` |
| Python library/dev  | `uv pip install autocontext==0.15.1`  |
| TypeScript/Node CLI | `bun add -g autoctx@0.15.1`           |
| Pi extension        | `pi install npm:pi-autocontext@0.10.0` |

The PyPI package is `autocontext`; the CLI is `autoctx`. The npm package is `autoctx` (not the unrelated `autocontext` npm package). Provider variables live in [`.env.example`](.env.example).

## 30-Second Run

Pi is the lowest-friction provider because it uses your local agent auth:

```bash
AUTOCONTEXT_AGENT_PROVIDER=pi \
AUTOCONTEXT_PI_COMMAND=pi \
autoctx solve "improve customer-support replies for billing disputes" --iterations 3
```

Use `AUTOCONTEXT_AGENT_PROVIDER=anthropic`, `openai-compatible`, `openrouter`, `claude-cli`, `codex`, `pi-rpc`, or another provider when you need that runtime. See [agent integration](autocontext/docs/agent-integration.md) for the full matrix.

Running it on your own GPU instead? [Self-hosted models](autocontext/docs/self-hosted-models.md) covers the whole loop on vLLM, Ollama, or any OpenAI-compatible endpoint — including what each role actually resolves to, and why constrained output matters more on open weights.
Self-hosted endpoints can additionally declare `AUTOCONTEXT_PROVIDER_HOSTING=local` and a `fast`, `mid_tier`, or `frontier` `AUTOCONTEXT_PROVIDER_CAPABILITY`; role-specific endpoints use matching `<ROLE>_PROVIDER_*` declarations.

## Agent Entry Points

- **Pi:** install `pi-autocontext`, then ask Pi to solve, judge, improve, list, or inspect runs through the packaged skill.
- **MCP clients:** run `autoctx serve mcp` or `bunx autoctx serve mcp` and expose the tools to Claude Code, Cursor, or another MCP client.
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
| `status`      | `autoctx status <run-id> --json`                        | Read one run snapshot                                   |
| `watch`       | `autoctx watch <run-id> --ndjson`                       | Stream run snapshots                                    |
| `show`        | `autoctx show <run-id> --best --json`                   | Inspect a selected generation                           |
| `simulate`    | `autoctx simulate -d "..."`                             | Model/replay/compare system behavior                    |
| `investigate` | `autoctx investigate -d "..."`                          | Evidence-driven diagnosis                               |
| `mission`     | `autoctx mission create --name "..." --goal "..."`      | Verifier-driven multi-step goals                        |
| `train`       | `uv run autoctx train --scenario <name> --data <jsonl>` | Distill stable behavior into a cheaper runtime (Python) |
| `serve mcp`   | `autoctx serve mcp`                                     | Give an agent the autocontext tool surface              |

Running bare `autoctx` shows the concise paved-road workflow. Use `autoctx
--help --all` in the npm CLI or `autoctx commands --all` in the Python CLI for
the full catalog. `--iterations` is the primary iteration flag; `--gens` is a
compatibility alias. `autoctx --version --json` reports the package version and
runtime (`python` or `typescript`).

Python owns the full control-plane package; TypeScript owns several operator-facing surfaces, the TUI, and Node runtime adapters. Start with [autocontext/README.md](autocontext/README.md) or [ts/README.md](ts/README.md).

<!-- autocontext-whats-new:start -->
## What's New in 0.15.1

- **Local models as a first-class peer tier:** endpoints now declare hosting (`local`/`remote`) and capability (`fast`/`mid_tier`/`frontier`) separately, so a self-hosted frontier-class model is no longer permanently misclassified as mid-tier. Per-provider model defaults stop leaking Claude ids into local endpoints, providers that serve real tiers get a model per tier, and `autoctx run` preflights every configured endpoint before spending generation tokens.
- **Offline mode:** `AUTOCONTEXT_OFFLINE=1` enforces that the Python engine never initiates an outbound connection, verified by a test that runs a full generation with a socket-level guard and asserts zero connection attempts. Operator-initiated access such as SSH stays in scope, so an airgapped host does not have to be unreachable. The TypeScript engine refuses to start rather than run unenforced.
- **Schema-enforced role output:** analyst, coach and architect responses are constrained to a JSON Schema on OpenAI-compatible and Anthropic backends instead of being scraped out of Markdown headings. Measured on llama3.1:8b, analyst format drift went from 100% to 0%. Set `AUTOCONTEXT_CONSTRAINED_OUTPUT=false` to keep the previous behavior.
- **Judge scoring correctness:** a reasoning block emitted before the answer could win the judge's score parse, recording 0.05 for a run the judge scored 0.88. Both engines now share one model-JSON extractor that prefers the answer over the scratchpad.
- **Refreshed model ids:** shipped defaults move to Claude Opus 5 / Sonnet 5 and the GPT-5.6 family. Cost attribution moves with them; the previous table still priced Opus 4.6 at $15/$75 per M, overstating spend on a default run by roughly 3x.
<!-- autocontext-whats-new:end -->

## Scenario Families

The shipped families cover games, agent tasks, simulations, artifact editing, investigations, workflows, negotiation, schema evolution, tool fragility, operator loops, and coordination. Python and TypeScript share the family vocabulary; see [docs/internal/scenario-parity-matrix.md](docs/internal/scenario-parity-matrix.md) for parity details.

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

## Acknowledgments

Thanks to [George](https://github.com/GeorgeH87) for generously donating the `autocontext` name on PyPI.
