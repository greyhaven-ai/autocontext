# Docs Overview

This directory is the maintainer-facing landing page for repository docs. Use it to find the right guide quickly and keep public documentation aligned when the repo changes.

## Start Here

- [Repository overview](../README.md)
- [Canonical concept model](concept-model.md)
- [Copy-paste examples](../examples/README.md)
- [Change history](../CHANGELOG.md)

## Using The Packages

- [Python package guide](../autocontext/README.md)
- [TypeScript package guide](../ts/README.md)
- [Demo data notes](../autocontext/demo_data/README.md)

Public examples use the canonical nested CLI paths: `autoctx scenario create`
for scenario authoring and `autoctx serve mcp` for MCP clients.

## Integrating External Agents

- [External agent integration guide](../autocontext/docs/agent-integration.md)
- [Running the loop on your own hardware](../autocontext/docs/self-hosted-models.md)
- [Hermes Curator + autocontext positioning](hermes-positioning.md)
- [Python and TypeScript extension hooks](../autocontext/docs/extensions.md)
- [Sandbox and executor notes](../autocontext/docs/sandbox.md)
- [Capability-scoped research workspaces](research-workspaces.md)
- [Provider-neutral remote execution sessions](remote-execution-sessions.md)
- [Persistent host worker](../autocontext/docs/persistent-host.md)
- [Correctness-first external kernel evolution](../autocontext/docs/kernel-evolution.md)
  — including typed provider generation, durable budgets, status/stop, and
  crash-safe resume.
- [MLX host training notes](../autocontext/docs/mlx-training.md)
- [Case study: recursive loop closed on local MLX](../autocontext/docs/case-study-recursive-loop.md)

## Contributing And Support

- [Contributing guide](../CONTRIBUTING.md)
- [Agent guide](../AGENTS.md)
- [Support](../SUPPORT.md)
- [Security policy](../SECURITY.md)

## Architecture And Parity

- [Cross-runtime CLI contract guide](cli-contract.md), [machine-readable contract](cli-contract.json), [wire-output schemas](cli-schemas/), and [shared fixtures](cli-fixtures/)
- [Interactive WebSocket protocol contract](websocket-protocol-contract.json)

The contract pins the TypeScript-only `agent_progress_notes_v1` extension,
including its exact Autowork-compatible fixture, safe-copy and size limits,
earlier same-run evidence rules, exact durable replay, and finite retention.
Python intentionally does not advertise it until equivalent durable transcript
metadata is available.

- [Core/control package split](core-control-package-split.md)
- [Runtime component lifecycle](internal/runtime-component-lifecycle.md)
- [Runtime effect policy](internal/runtime-effect-policy.md)
- [Runtime component graph](internal/runtime-component-graph.md)
- [Transactional runtime activation](internal/runtime-transactional-activation.md)
- [Runtime composition confluence harness](internal/runtime-composition-confluence.md)
- [Strategy package import side-effect contract](strategy-package-import-contract.json)
- [Generic edge runtime compatibility spike](edge-runtime-compatibility.md)
- [Fetch adapter API reference](fetch-api-reference.md)
- [Fetch host capability manifest examples](fetch-host-capability-manifest.md)
- [Generated Fetch packaging guide](generated-fetch-packaging.md)
- [Fetch conformance guide](fetch-conformance.md)
- [Fetch adapter troubleshooting guide](fetch-troubleshooting.md)
- [Flue-inspired runtime decisions](flue-influences.md)
- [Scenario parity matrix — Python & TypeScript](scenario-parity-matrix.md)
- [Scenario environment contract](scenario-environment-contract.md)
- [Role routing contract](role-routing-contract.json)
- [Typed rubric contract](rubric-spec.md)
- [Run progress report](run-progress-report.md)
- [Run utilization report](run-utilization-report.md)
- [Negative result ledger](negative-result-ledger.md)
- [Campaign mode report](campaign-mode-report.md)
- [Optional campaign scheduler](campaign-scheduler.md)
- [Read-only campaign auditor](campaign-auditor.md)
- [Goal run report](goal-run-report.md)
- [Playbook approval gate](playbook-approval-gate.md)
- [Immutable context bundles and outcome-gated promotion](context-bundles.md)
- [Campaign false-promotion calibration](false-promotion-calibration.md)
- [Derived lesson curation](derived-lesson-curation.md)
- [Soft structural hints](soft-structural-hints.md)
- [Span-level credit attribution](span-credit-attribution.md)
- [Ablation-backed context attribution](context-attribution.md)
- [Trainer-local statistical confirmation](training-statistical-confirmation.md)
- [OPD/GKD + GRPO mixture experiment protocol](opd-grpo-mixture-experiment.md)
- [Exploration collapse guard](exploration-collapse-guard.md)
- [Browser exploration contract](browser-exploration-contract.md)
- [OpenTelemetry bridge](opentelemetry-bridge.md)
- [Background session domain and parity contract](background-session-domain.md)
- [Background execution trust boundaries and credential model](background-execution-trust-boundaries.md)

## Execution Surfaces

- **`simulate`** — modeled-world exploration with sweeps, replay, compare, export
- **`investigate`** — evidence-driven diagnosis in synthetic harness or iterative LLM modes
- **`analyze`** — interpret and compare outputs from all surfaces
- **`context-selection`** — inspect persisted prompt context telemetry for run budget/cache tuning
- **`mission`** — real-world goal execution with adaptive planning and campaigns
- **`agent`** — TypeScript local runner/dev server and self-hosted Node build target for experimental `.autoctx/agents` handlers
- **`tui`** — Node 22.19+ pi-tui operator client for local runs or remote
  TypeScript server attachment, with durable transcript replay and cockpit reads
- **`train`** — distill curated datasets into scenario-local models
- **`hermes`** — read-only Hermes v0.12 skill/Curator inspection plus Hermes skill export

## Trace Pipeline

- Public trace schema v1.0.0 for cross-harness interchange
- Privacy-aware export with sensitive-data redaction (21 patterns)
- Publishing to local JSONL, GitHub Gist, Hugging Face (ShareGPT format)
- Dataset curation with gate filtering, top-quartile selection, held-out splits
- Model selection strategy (from-scratch / LoRA / full fine-tune)
- Training backends (MLX / CUDA) with promotion lifecycle

## Maintainer Docs

- [Analytics and adoption guide](analytics.md)
- [Release checklist](release-checklist.md)

## Keep These In Sync

If a change affects commands, package names, published versions, environment variables, agent integration flows, or support expectations, review these docs in the same PR:

- `README.md`
- `autocontext/README.md`
- `ts/README.md`
- `examples/README.md`
- `autocontext/docs/agent-integration.md`
- `CHANGELOG.md`
- `SUPPORT.md`
