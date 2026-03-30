# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.3.0] - 2026-03-29

### New Commands

- **`autoctx simulate`** — plain-language multi-variable simulation with sweeps, replay, compare, and export (AC-446, AC-450, AC-451, AC-452, AC-454)
- **`autoctx investigate`** — evidence-driven diagnosis with hypotheses, confidence scoring, and unknowns (AC-447)
- **`autoctx analyze`** — interpret and compare runs, simulations, investigations, and missions (AC-448)
- **`autoctx train`** — train distilled models from curated datasets with backend selection (AC-460)
- **Python `autoctx simulate`** — full parity with TS surface: run, replay, compare, export (AC-453)

### Scenarios

- All 11 scenario families now fully executable in TypeScript (was 2/11) via secure-exec V8 isolate codegen (AC-436)
- `operator_loop` is now a fully runnable family in both packages (AC-432)
- Unified family classifier — all families reachable through CLI (AC-437)
- Spec auto-heal: codegen failures trigger automatic recovery (AC-440)
- Scenario revision flow: refine created scenarios with feedback (AC-441)
- Deep execution validation: generated code executed and verified before registration (AC-442)
- 3 scenario templates: content-generation, prompt-optimization, rag-accuracy (AC-443)
- `new-scenario` CLI materializes runnable artifacts to disk (AC-433)
- Scenario parity matrix documenting Python/TypeScript surface coverage (AC-431)

### Missions & Campaigns

- Adaptive mission execution: LLM-driven goal decomposition and step planning replaces generic bookkeeping (AC-435)
- Campaign abstraction: coordinate multiple missions under long-term goals with budget tracking and dependencies (AC-428)
- Mission-simulation integration: missions invoke simulations as planning tools (AC-455)

### Trace Pipeline

- Open public trace schema v1.0.0: versioned interchange format for coding agent traces (AC-462)
- Sensitive-data detection and redaction: 21 built-in patterns with policy-backed actions (AC-464, AC-468)
- Privacy-aware trace export workflow: redact → validate → manifest → attestation (AC-463)
- Publishing connectors: local JSONL, GitHub Gist, and Hugging Face (ShareGPT format) (AC-465)
- Trace-to-model data plane: DatasetCurator + DataPlane orchestrator (AC-466)
- Repo-local dataset discovery: scan repo trees, convert JSONL/JSON/CSV to ShareGPT (AC-461)
- Curated distillation dataset pipeline: gate filtering, top-quartile, family filtering, failure-example policy (AC-458)

### Training & Distillation

- Base model selection: maps scenario families to training modes (from-scratch, LoRA, full fine-tune) (AC-459)
- Training backend abstraction: MLX + CUDA with injectable TrainingExecutor hook (AC-460)
- Prompt alignment: training ↔ runtime contract ensures distilled models match runtime invocation (AC-457)
- Candidate-shadow-active promotion lifecycle with configurable quantitative gates and rollback (AC-456)

### Infrastructure

- Consolidated operator UI: the Python `serve` / `tui` surfaces are API/WebSocket-first, while interactive terminal UI remains available through the TypeScript client surfaces (AC-467)
- Richer sweep DSL: categorical sweeps, logarithmic scales, sweep file loading, named presets (AC-454)

### Fixed

- Trace pipeline audit: expanded redaction patterns (21, was 12), ISO 8601 timestamp validation, explicit role mapping, export warnings, HF format fix (AC-468)
- Distillation audit: training executor hook, base model validation, CSV parser edge cases, silent catches → warnings, integration test (AC-468)

## [0.2.4] - 2026-03-26

### Added
- Session notebook context now flows into runtime prompts and cockpit views for active runs.
- World-state abstractions now support stateful scenario families and workflow-style scenarios.

### Changed
- Agent-task scaffolding and execution now use separate phased budgets.
- Operator-loop scenarios remain available as typed family metadata, but executable operator-loop scaffolding has been removed so the harness no longer bakes in escalation-specific runtime behavior.
- Public repo docs now include a docs landing page, package-selection guidance, an analytics/adoption guide, a release checklist, copy-paste integration examples for CLI, MCP, Python SDK, and TypeScript usage, plus README package/download signals.

### Fixed
- Python package fallback version metadata now matches the published `0.2.0` package version.

## [0.2.0] - 2026-03-15

### Added
- Initial public release with Python and TypeScript packages.
- Generation loop with Elo-based progression gating.
- Agent roles: competitor, analyst, coach, architect, curator.
- Pluggable scenarios: grid_ctf, othello, custom creation pipeline.
- LLM judge with multi-sample evaluation.
- Task runner daemon with improvement loops.
- MCP server with tool implementations.
- FastAPI dashboard with WebSocket events.
- CLI via Typer (Python) and parseArgs (TypeScript).
