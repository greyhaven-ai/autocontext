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

## Integrating External Agents

- [External agent integration guide](../autocontext/docs/agent-integration.md)
- [Hermes Curator + AutoContext positioning](hermes-positioning.md)
- [Python and TypeScript extension hooks](../autocontext/docs/extensions.md)
- [Sandbox and executor notes](../autocontext/docs/sandbox.md)
- [Persistent host worker](../autocontext/docs/persistent-host.md)
- [MLX host training notes](../autocontext/docs/mlx-training.md)

## Contributing And Support

- [Contributing guide](../CONTRIBUTING.md)
- [Agent guide](../AGENTS.md)
- [Support](../SUPPORT.md)
- [Security policy](../SECURITY.md)

## Architecture And Parity

- [Core/control package split](core-control-package-split.md)
- [Flue-inspired runtime decisions](flue-influences.md)
- [Scenario parity matrix — Python & TypeScript](scenario-parity-matrix.md)
- [Browser exploration contract](browser-exploration-contract.md)
- [OpenTelemetry bridge](opentelemetry-bridge.md)

## Execution Surfaces (0.3.0)

- **`simulate`** — modeled-world exploration with sweeps, replay, compare, export
- **`investigate`** — evidence-driven diagnosis in synthetic harness or iterative LLM modes
- **`analyze`** — interpret and compare outputs from all surfaces
- **`context-selection`** — inspect persisted prompt context telemetry for run budget/cache tuning
- **`mission`** — real-world goal execution with adaptive planning and campaigns
- **`agent`** — TypeScript local runner/dev server for experimental `.autoctx/agents` handlers
- **`train`** — distill curated datasets into scenario-local models
- **`hermes`** — read-only Hermes v0.12 skill/Curator inspection plus Hermes skill export

## Trace Pipeline (0.3.0)

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
