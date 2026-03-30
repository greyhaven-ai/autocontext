# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] - 2026-03-29

### New Commands

- **`autoctx simulate`** — plain-language multi-variable simulation with sweeps, replay, compare, and export.
- **`autoctx investigate`** — evidence-driven diagnosis with hypotheses, confidence scoring, and unknowns.
- **`autoctx analyze`** — interpret and compare runs, simulations, investigations, and missions.
- **`autoctx train`** — train distilled models from curated datasets with backend selection.
- **Python `autoctx simulate`** — full parity with the TypeScript surface: run, replay, compare, and export.

### Scenarios

- All 11 scenario families now fully executable in TypeScript (was 2/11) via secure-exec V8 isolate codegen.
- `operator_loop` is now a fully runnable family in both packages.
- Unified family classifier: all families reachable through the CLI.
- Spec auto-heal: codegen failures trigger automatic recovery.
- Scenario revision flow: refine created scenarios with feedback.
- Deep execution validation: generated code is executed and verified before registration.
- Three scenario templates: content-generation, prompt-optimization, and rag-accuracy.
- `new-scenario` CLI materializes runnable artifacts to disk.
- Scenario parity matrix documents Python/TypeScript surface coverage.

### Missions & Campaigns

- Adaptive mission execution: LLM-driven goal decomposition and step planning replaces generic bookkeeping.
- Campaign abstraction: coordinate multiple missions under long-term goals with budget tracking and dependencies.
- Mission-simulation integration: missions invoke simulations as planning tools.

### Trace Pipeline

- Open public trace schema v1.0.0: versioned interchange format for coding agent traces.
- Sensitive-data detection and redaction with policy-backed actions.
- Privacy-aware trace export workflow: redact, validate, manifest, and attestation.
- Publishing connectors for local JSONL, GitHub Gist, and Hugging Face.
- Trace-to-model data plane with `DatasetCurator` and `DataPlane`.
- Repo-local dataset discovery: scan repo trees and convert JSONL, JSON, CSV, and markdown into ShareGPT-style records.
- Curated distillation dataset pipeline with gate filtering, top-quartile selection, family filtering, and failure-example policy.

### Training & Distillation

- Base model selection maps scenario families to training modes (from-scratch, LoRA, and full fine-tune).
- Training backend abstraction with MLX and CUDA plus an injectable `TrainingExecutor` hook.
- Prompt alignment ensures distilled models match runtime invocation.
- Candidate-shadow-active promotion lifecycle with configurable quantitative gates and rollback.

### Infrastructure

- Consolidated operator UI: the Python `serve` and `tui` surfaces are API/WebSocket-first, while interactive terminal UI remains available through the TypeScript client surfaces.
- Richer sweep DSL: categorical sweeps, logarithmic scales, sweep file loading, and named presets.

### Fixed

- Trace pipeline audit: expanded redaction patterns, ISO 8601 timestamp validation, explicit role mapping, export warnings, and Hugging Face format fixes.
- Distillation audit: training executor hook, base model validation, CSV parser edge cases, silent catches now surfaced as warnings, and end-to-end integration coverage.

## [0.2.4] - 2026-03-26

### Added

- Session notebook context now flows into runtime prompts and cockpit views for active runs.
- World-state abstractions now support stateful scenario families and workflow-style scenarios.

### Changed

- Agent-task scaffolding and execution now use separate phased budgets.
- Operator-loop scenarios remain available as typed family metadata, but executable operator-loop scaffolding has been removed so the harness no longer bakes in escalation-specific runtime behavior.
- Public repo docs now include a docs landing page, package-selection guidance, an analytics/adoption guide, a release checklist, and copy-paste integration examples for CLI, MCP, Python SDK, and TypeScript usage.

### Fixed

- Python package fallback version metadata now matches the published `0.2.0` package version.

## [0.2.0] - 2026-03-15

### Added

- Initial public release with Python and TypeScript packages.
- Generation loop with Elo-based progression gating.
- Agent roles: competitor, analyst, coach, architect, and curator.
- Pluggable scenarios including `grid_ctf`, `othello`, and the custom creation pipeline.
- LLM judge with multi-sample evaluation.
- Task runner daemon with improvement loops.
- MCP server with tool implementations.
- FastAPI dashboard with WebSocket events.
- CLI via Typer (Python) and `parseArgs` (TypeScript).
