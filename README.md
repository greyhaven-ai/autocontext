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
| Python CLI          | `uv tool install autocontext==0.16.1` |
| Python library/dev  | `uv pip install autocontext==0.16.1`  |
| TypeScript/Node CLI | `bun add -g autoctx@0.16.1`           |
| Pi extension        | `pi install npm:pi-autocontext@0.10.0` |

The PyPI package is `autocontext`; the CLI is `autoctx`. The npm package is `autoctx` (not the unrelated `autocontext` npm package). Provider variables live in [`.env.example`](.env.example).
The npm CLI and TUI require Node.js 22.19.0 or newer; contributors should use
the version pinned in [`ts/.nvmrc`](ts/.nvmrc).

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
├── tools/
└── context_bundles/{bundles,candidates,promotions,active.json}
```

Everything is filesystem-first: inspect it, diff it, replay it, export it, or feed it into training.
Coach and architect context changes are stored as immutable candidates and are
not served until matched candidate/incumbent trials confirm them. See
[context bundles and outcome-gated promotion](docs/context-bundles.md).
Controlled component trials feed
[ablation-backed attribution](docs/context-attribution.md), so prompt selection
can demote low-value context without presenting edit-size correlation as causal.

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
| `scenario`    | `autoctx scenario create --help`                          | Create from a description, template, or harness spec    |
| `mission`     | `autoctx mission create --name "..." --goal "..."`      | Verifier-driven multi-step goals                        |
| `train`       | `uv run autoctx train --scenario <name> --data <jsonl>` | Distill stable behavior into a cheaper runtime (Python) |
| `serve mcp`   | `autoctx serve mcp`                                     | Give an agent the autocontext tool surface              |
| `tui`         | `autoctx tui [--connect <server>]`                       | Operate or attach to a run from the pi-tui terminal UI  |

Running bare `autoctx` shows the concise paved-road workflow. Use `autoctx
--help --all` in the npm CLI or `autoctx commands --all` in the Python CLI for
the full catalog. `--iterations` is the primary iteration flag; `--gens` is a
compatibility alias. `autoctx --version --json` reports the package version and
runtime (`python` or `typescript`).

Python owns the full control-plane package; TypeScript owns several operator-facing surfaces, the TUI, and Node runtime adapters. Start with [autocontext/README.md](autocontext/README.md) or [ts/README.md](ts/README.md).

<!-- autocontext-whats-new:start -->
## What's New in 0.16.1

- **A clearer paved-road CLI:** `run` now requires an explicit scenario, `--iterations` is the primary spelling, concise help leads with the core workflow, and `autoctx commands --all` exposes the full catalog. Existing `--gens` and legacy command aliases remain available for compatibility.
- **Stable machine-readable inspection:** `status`, `show`, and `watch --ndjson` emit versioned, schema-backed envelopes with consistent stdout, stderr, and exit-code behavior. Status and show accept `--run-id`, while `--version --json` identifies the Python runtime and package version.
- **More reliable run inspection:** `watch` no longer exits before a completed generation becomes visible, and `show` defaults to the latest generation while rejecting conflicting selectors instead of guessing.
- **Portable exports by default:** `export` now writes JSON to stdout when no output path is supplied, supports Pi packages, and retains `strategy` as a compatibility alias for the default JSON format.
- **Contracts ship with the package:** CLI contract v2 schemas and shared fixtures are included in wheels and source distributions so downstream tools can validate the same status, show, queue, and export shapes as the CLI.
- **Ratcheted package boundaries:** domain, analytics, configuration, and storage implementations now follow enforced dependency directions while legacy module paths remain available as compatibility shims.
<!-- autocontext-whats-new:end -->

### npm runtime highlights included in 0.16.1

The aligned `autoctx@0.16.1` package also carries the TypeScript-first runtime
work introduced in 0.16.0 and hardened in 0.16.1:

- **Host-owned live composition:** typed runtime capabilities, scoped cleanup
  and effect policies, reactive component graphs, and durable transactional
  activation/rollback for trusted hosts.
- **A production-oriented operator TUI:** the pi-tui client supports local and
  remote attachment, durable replay, run control and inspection, and bounded,
  redacted terminal state on Node.js 22.19+.
- **Image-aware interactive sessions:** compatible TypeScript providers can
  advertise `image_attachments_v1`; attachment validation is bounded and
  fail-closed before provider inference.
- **Protocol and terminal hardening:** exact capability negotiation, protected
  priority controls, bounded WebSocket resources, credential redaction, and
  terminal-control sanitization are enforced across the interactive path.

Python parity for the pi-tui client and image attachments remains deferred.
See the [TypeScript guide](ts/README.md), [runtime composition
contracts](docs/internal/runtime-component-graph.md), and the full
[changelog](CHANGELOG.md) for details.

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

[![Star History Chart](https://star-history.dera.page/svg?repos=greyhaven-ai/autocontext&type=Date)](https://star-history.dera.page/#greyhaven-ai/autocontext&Date)

## Acknowledgments

Thanks to [George](https://github.com/GeorgeH87) for generously donating the `autocontext` name on PyPI.
