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

Experimental Python research: the [Qwen weight-space reproduction](examples/weight_evolution/qwen_reproduction/README.md)
pins model/data/scorer assets, verifies the published checkpoint, and provides bounded paired inference with replayable reproduction reports.
The [GPU acceptance workflow](examples/weight_evolution/qwen_reproduction/GPU_ACCEPTANCE.md) gates full execution; live reproduction remains pending.
The [comparison-condition harness](examples/weight_evolution/qwen_comparison/README.md) exercises baseline, context, weight, combined and decoding conditions with offline synthetic fixtures.
The [CPU-only editing sessions](examples/weight_evolution/qwen_sessions/README.md) create context, weight-descriptor and decoding candidates with finite durable budgets, practice-only adapter views and sealed exports into that comparison harness. Real GPU editing and benchmark practice selection remain deferred.
The [adaptive editing fixtures](examples/weight_evolution/qwen_adaptive/README.md) add feedback-conditioned proposals, equal search caps, practice-based selection, and a separate confirmation gate before the final fixture audit.
The [checkpoint-backed editing fixtures](examples/weight_evolution/qwen_checkpoints/README.md) apply typed edits to tiny CPU safetensors and carry independently verified input/program/output hashes through selection and confirmation. Model and GPU inference remain deferred.
The [CPU inference study](examples/weight_evolution/cpu_inference/README.md) runs a pinned tiny classifier on verified checkpoint bytes and measures baseline, context, weight, combined, and decoding conditions. Its constructed tasks establish execution correctness; Qwen reproduction and GPU execution remain deferred.
The [adaptive CPU search](examples/weight_evolution/cpu_adaptive/README.md) uses those measured practice scores to choose bounded context, tensor, and decoding edits, restore an earlier winner, and freeze selection before confirmation. Linux acceptance covers real memory/CPU limit failures and cleanup; GPU execution remains deferred.
The [generated CPU campaign](examples/weight_evolution/cpu_campaign/README.md) compares adaptive, fixed-order, and seeded-random search at equal capacity across pinned task instances. Complete schedule accounting, read-only replay, and paired task-level reports retain failures and vetoes without treating repeated seeds as independent tasks.

The [CPU search diagnostics](examples/weight_evolution/cpu_diagnostics/README.md) freeze completed selections before probing every declared single-surface option on practice cases. Separate reference costs and descriptive reports distinguish unavailable improvements, missed options, duplicate outputs, and confirmation vetoes. GPU execution remains deferred.

The [controlled CPU transfer experiments](examples/weight_evolution/cpu_controls/README.md) add deterministic weight-repair, already-correct and practice-shortcut controls. A separate pinned contract preserves earlier evidence while family-specific reports test transfer under the unchanged confirmation gate. These constructed controls do not establish benchmark gains; GPU execution remains deferred.

The [CPU failure supervisor](examples/weight_evolution/cpu_failures/README.md) records bounded live exception chains under a separate implementation pin. It preserves earlier journals, keeps unavailable observations unknown, and runs one declared CPU campaign with conditional practice references. GPU execution remains deferred.

The [measured CPU outcomes](examples/weight_evolution/cpu_measured/README.md) add a pinned v2 failure codec, deterministic prompt redaction, and process measurements that survive errors. A small declared comparison checks native-versus-measured receipt parity without retries. Earlier evidence remains intact; GPU execution is deferred.

The [measured CPU search integration](examples/weight_evolution/cpu_execution/README.md) carries those outcomes through practice, restoration, confirmation, conditional audit, and exhaustive practice references. A pinned 18-search native-versus-measured schedule checks semantic parity and preserves failed or uncertain operations and their charges. GPU execution remains deferred.

The [full measured CPU pilot](examples/weight_evolution/cpu_pilot/README.md) declares 72 searches and 24 conditional practice references over the unchanged control tasks. Its CPU-only Linux/amd64 acceptance job retains complete or partial evidence, separates integrity from completion and calibration, and verifies replay without dispatch or writes. GPU execution remains deferred.

The [budget-limited CPU search landscapes](examples/weight_evolution/cpu_search_landscapes/README.md) pin informative, misleading and neutral weight-search tasks with independent oracle expectations. Thirty-six equal-budget searches and twelve practice-only references measure expected wins, losses and ties, practice progress and resource usage. Context and decoding are neutral controls; the unchanged joint gate withholds audit. GPU execution remains deferred.

The [generated CPU joint controls](examples/weight_evolution/cpu_joint_controls/README.md) pin positive, correct-baseline and practice-only repair tasks with an independent deterministic oracle. Eighteen equal-budget searches and six conditional practice references test component gains, the unchanged joint gate and actual audit routing. These constructed, seed-conditional controls do not establish adaptive superiority or benchmark gains; GPU execution remains deferred.

## Install

For Python CUDA/SFT installation, follow the [maintained Accelerate build guide](docs/security/accelerate-risk-assessment.md).
Published installations must install the bundled security build before adding the CUDA extra; source checkouts use the locked uv source.

| Surface             | Command                               |
| ------------------- | ------------------------------------- |
| Python CLI          | `uv tool install autocontext==0.19.1` |
| Python library/dev  | `uv pip install autocontext==0.19.1`  |
| TypeScript/Node CLI | `bun add -g autoctx@0.19.1`           |
| Pi extension        | `pi install npm:pi-autocontext@0.12.0` |

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

Prime remote execution also supports opt-in accelerator requests with explicit
type/count, immutable-image, region, and telemetry capability validation. It
fails before provider creation when the configured pool cannot satisfy the
request and never downgrades accelerator work to CPU; see
[remote execution sessions](docs/remote-execution-sessions.md).
Shipped Prime generation and campaign paths persist a durable pre-dispatch
claim plus the complete result/ledger projection before returning paid work;
restart never treats an unresolved or already committed request as permission
to provision another sandbox.

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
Kernel campaigns extend that contract with exact provider-generation receipts,
bounded paid-call accounting, content-addressed lineage, and safe stop/status/resume.
Coach and architect context changes are stored as immutable candidates and are
not served until matched candidate/incumbent trials confirm them. The live
serving boundary can additionally require a cancellable independent audit and
a durable campaign-wide false-promotion budget; exact causal credit is accepted
only from verified single-component manifest additions. See
[context bundles and outcome-gated promotion](docs/context-bundles.md).
Python also provides an opt-in [executable-skill bridge](autocontext/docs/executable-skills.md)
for verified schema-migration proposals in Docker, with explicit eligibility
and promotion-gated serving.
An opt-in [skill/model router](autocontext/docs/skill-routing.md) adds verified
fallback and explicit abstention under one request budget.
The [schema-migration reuse study](autocontext/benchmarks/skill_reuse/README.md)
compares baseline, textual context, executable reuse and cheaper-model controls
under frozen paired evaluation; fixture runs are infrastructure evidence only.
Controlled component trials feed
[ablation-backed attribution](docs/context-attribution.md), so prompt selection
can demote low-value context without presenting edit-size correlation as causal.

Python kernel evolution can also compose bounded studies across variable-shape
matmul, fused elementwise/reduction, and causal-attention families. Each family
retains independent primary/confirmation evidence and per-case floors;
cross-shape, cross-hardware, and cross-family trials distinguish portable,
partially transferring, specialist, and plateau outcomes without an aggregate
score hiding a failed workload. See the
[kernel evolution guide](autocontext/docs/kernel-evolution.md#multi-workload-studies).

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

HTTP and WebSocket control planes bind to loopback by default. A non-loopback
bind fails closed unless `AUTOCONTEXT_SERVER_TOKEN` contains a random value of
at least 32 characters. Send it as an `Authorization: Bearer` value; browser
WebSocket clients use the `autocontext.bearer.<base64url-token>` subprotocol.
Tokens in endpoint URLs are rejected. See the
[persistent-host security model](autocontext/docs/persistent-host.md#trust-and-credential-boundary)
before exposing a server beyond one trusted operator.

<!-- autocontext-whats-new:start -->
## What's New in 0.19.0

- **Safer run re-entry:** autoctx resume continues a run with its stored scenario and target, completed runs stay unchanged, and re-entering under a different provider or executor mode is refused unless resume gets --allow-runtime-change. Agent-task runs, A/B tests and TypeScript runs refuse run ids that already exist.
- **Reliable shared databases:** several processes can start against one new SQLite database without migration-ledger or WAL lock failures, and cross-runtime migrations keep runs.minimum_generations, re-adding it where an earlier migration dropped it.
- **Correct artifact paths:** Python run artifacts no longer land at doubled paths under relative artifact roots, and artifact_write hooks in either runtime cannot redirect a write out of its managed root through a symlink.
- **Opt-in executable skills (Python):** for a schema-migration pilot, context bundles can carry Docker-isolated executable skills routed with verified model fallback, and a complete routed policy is promoted only through a fail-closed quality and cost gate. Nothing is enabled automatically.
- **Evaluator provenance and human labels (Python):** judge epochs bind an immutable serving specification that includes calibration examples, and autoctx labels collects human-only judge labels with random audits and protected holdout splits.
- **Measured reuse studies:** archived GridCTF policy-reuse and schema-migration reuse studies report quality, fallback and resource costs. GridCTF dollar costs are unknown, the schema-migration study records model-credit charges but not full lifecycle cost, and neither study authorizes production promotion.
<!-- autocontext-whats-new:end -->

### npm 0.17.3 minimum iteration floors

`autoctx@0.17.3` advertises `minimum_iterations_v1`. Structured task contracts
may set `minimumIterations`, interactive `start_run` commands may override it
with `minimum_generations`, and the effective value must be between one and the
maximum iteration count. Normal quality, plateau, unchanged-output, and cached
verdict convergence cannot stop below the floor; cancellation, provider or
evaluator failure, and time/token safety exits remain immediate. The effective
minimum is retained in live protocol metadata and SQLite-backed run history so
Cockpit clients recover it after restart.

### npm 0.17.2 structured tasks and durable outcomes

`autoctx@0.17.2` is a TypeScript-only patch release; the Python package remains
at `autocontext==0.17.0`. The npm package adds:

- **Structured desktop missions:** protocol-v2 servers advertise
  `structured_task_creation_v1` and accept strict, versioned `create_task`
  commands with bounded source contents, explicit data roles, retained
  provenance, integrity checks, ordered setup/run progress, and durable
  multi-round results.
- **Privacy-preserving evaluation:** evaluator-only sources can affect scores
  without entering candidate prompts, revision feedback, transcripts, or
  retained analyst output.
- **Bounded artifact continuation:** truncated initial or revised task outputs
  can continue within explicit segment and total-size limits; exhausted or
  non-growing continuation fails closed before evaluation.
- **Durable task outcomes:** completed saved agent tasks advertise
  `agent_task_outcome_v1`, retain a compact terminal receipt and per-generation
  evaluator evidence, and expose the complete versioned outcome through
  SQLite-backed Cockpit inspection.

The package also carries the TypeScript-first runtime work introduced in 0.16.0
and hardened in 0.16.1:

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

Python parity for structured task creation and outcomes, the pi-tui client, and
image attachments remains deferred.
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

## Acknowledgments

Thanks to [George](https://github.com/GeorgeH87) for generously donating the `autocontext` name on PyPI.
