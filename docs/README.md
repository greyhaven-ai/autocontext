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
- [Sandbox and executor notes](../autocontext/docs/sandbox.md)
- [MLX host training notes](../autocontext/docs/mlx-training.md)

## Contributing And Support

- [Contributing guide](../CONTRIBUTING.md)
- [Agent guide](../AGENTS.md)
- [Support](../SUPPORT.md)
- [Security policy](../SECURITY.md)

## Architecture And Parity

- [Scenario parity matrix — Python & TypeScript](scenario-parity-matrix.md)

## Execution Surfaces (0.3.0)

- **`simulate`** — modeled-world exploration with sweeps, replay, compare, export
- **`investigate`** — evidence-driven diagnosis with hypotheses and confidence
- **`analyze`** — interpret and compare outputs from all surfaces
- **`mission`** — real-world goal execution with adaptive planning and campaigns
- **`train`** — distill curated datasets into scenario-local models

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
