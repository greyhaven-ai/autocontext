# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

Changes in this section are on the branch/repo after `0.3.6` and are not part of the last published release until the next version is cut.

### Fixed

- Python `autoctx simulate` now resolves live generation through the effective architect-role runtime surface, so `AUTOCONTEXT_ARCHITECT_PROVIDER` and other role-routing overrides are honored instead of being bypassed by the raw client builder.
- Python simulation spec normalization now tolerates LLM-friendly action/spec shapes such as `postconditions`, nested criteria objects, and extra action-planning metadata without failing code generation.
- Structured simulation preconditions now preserve referenced action ids when LLM output includes both an `action` field and human-readable prose, so generated dependencies remain executable.
- Regenerating a custom scenario with the same name in one process now force-reloads the generated module so `solve` and creator validation do not reuse stale scenario classes from `sys.modules`.
- Pi-backed live flows now default to a 300 second timeout, reducing spurious failures in longer `solve` runs.
- Public docs now describe `operator-in-the-loop` as a runnable family and no longer contradict the executable tests.

## [0.3.6] - 2026-04-07

### Changed

- Hardened bootstrap, evidence, and privacy handling so environment snapshots redact shell paths correctly, rematerialized workspaces do not retain stale artifacts, and live prompt/evidence flows now wire the collected snapshot and evidence manifest into the real loop.
- Tightened scenario-generation safety in the TypeScript surface so `operator_loop` validation requires its real escalation/clarification hooks and spec auto-heal preserves punctuation-heavy precondition dependencies instead of dropping valid ordering.
- Improved evidence and security backstops by failing closed on TruffleHog execution errors and making the evidence workspace/MCP integration rely on a materialized runtime workspace instead of dead helper-only paths.
- Hardened blob-store backends so local keys cannot escape the configured root and Hugging Face bucket metadata/list/delete behavior remains accurate across fresh process boundaries.
- Python and TypeScript package metadata are bumped to `0.3.6`.

## [0.3.5] - 2026-04-06

### Changed

- Stabilized the post-`0.3.4` simulation path so operator-loop scenarios preserve behavioral-contract signals across multi-run, sweep, and replay flows instead of silently dropping them.
- Hardened plain-language simulation execution around explicit family detection, operator-loop contract enforcement, and shared CLI engine-result handling so incomplete runs surface consistently across Python and TypeScript surfaces.
- Tightened the simulation-engine implementation without regressing the repo module-size guardrail, including the compatibility shim needed by existing abstract-class filtering tests.
- Python and TypeScript package metadata are bumped to `0.3.5`.

## [0.3.4] - 2026-04-04

### Changed

- Added action-label and living-docs surfaces to the operator workflow, including reviewer-driven cleanup on the action-label taxonomy and living-docs maintenance path.
- Landed the TypeScript/Python parity tranche for session store and the full research package, keeping the rebased cross-surface runtime behavior aligned on current `main`.
- Folded in the `pi-autocontext` polish follow-up so the published Pi package line reflects the renamed extension and its best-practices cleanup.
- Python and TypeScript package metadata are bumped to `0.3.4`.

## [0.3.3] - 2026-04-03

### Changed

- Expanded the research surface with validated domain contracts, runtime gating, persistence hardening, and better evaluation wiring for briefs, prompts, and adapters.
- Hardened Python and TypeScript operator-control surfaces around terminal lifecycle transitions, remote approvals, progress digests, and agentOS session/runtime error handling.
- Improved SQLite bootstrap and migration compatibility so packaged installs and fresh databases stay aligned with the live generation schema.
- Expanded the TypeScript provider compatibility surface with env-driven config for `gemini`, `mistral`, `groq`, `openrouter`, and `azure-openai`, and synced the public provider docs/tests to match.
- Python and TypeScript package metadata are bumped to `0.3.3`.

## [0.3.2] - 2026-04-02

### Changed

- Completed the TypeScript session-runtime parity pass across lifecycle management, coordinator state transitions, supervision, context pressure, remote approvals, progress digests, memory consolidation, and skill registry behavior.
- Hardened the TypeScript operator control plane so terminal session and worker states stay terminal, remote approvals require connected controllers, and redirected work remains visible in progress summaries.
- Python and TypeScript package metadata are bumped to `0.3.2`.

## [0.3.1] - 2026-04-01

### Changed

- Python package publishing now uses the canonical PyPI name `autocontext` instead of `autoctx`.
- Public install docs now reflect the package split accurately: PyPI is `autocontext`, while npm remains `autoctx`.
- Python and TypeScript package metadata are bumped to `0.3.1`.

## [0.3.0] - 2026-03-29

### Added

#### Commands

- **`autoctx simulate`** — plain-language multi-variable simulation with sweeps, replay, compare, and export.
- **`autoctx investigate`** — evidence-driven diagnosis with hypotheses, confidence scoring, and unknowns.
- **`autoctx analyze`** — interpret and compare runs, simulations, investigations, and missions.
- **`autoctx train`** — train distilled models from curated datasets with backend selection.
- **Python `autoctx simulate`** — full parity with the TypeScript surface: run, replay, compare, and export.

#### Scenarios

- All 11 scenario families now fully executable in TypeScript (was 2/11) via secure-exec V8 isolate codegen.
- `operator_loop` is now a fully runnable family in both packages.
- Unified family classifier: all families reachable through the CLI.
- Spec auto-heal: codegen failures trigger automatic recovery.
- Scenario revision flow: refine created scenarios with feedback.
- Deep execution validation: generated code is executed and verified before registration.
- Three scenario templates: content-generation, prompt-optimization, and rag-accuracy.
- `new-scenario` CLI materializes runnable artifacts to disk.
- Scenario parity matrix documents Python/TypeScript surface coverage.

#### Missions & Campaigns

- Adaptive mission execution: LLM-driven goal decomposition and step planning replaces generic bookkeeping.
- Campaign abstraction: coordinate multiple missions under long-term goals with budget tracking and dependencies.
- Mission-simulation integration: missions invoke simulations as planning tools.

#### Trace Pipeline

- Open public trace schema v1.0.0: versioned interchange format for coding agent traces.
- Sensitive-data detection and redaction with policy-backed actions.
- Privacy-aware trace export workflow: redact, validate, manifest, and attestation.
- Publishing connectors for local JSONL, GitHub Gist, and Hugging Face.
- Trace-to-model data plane with `DatasetCurator` and `DataPlane`.
- Repo-local dataset discovery: scan repo trees and convert JSONL, JSON, CSV, and markdown into ShareGPT-style records.
- Curated distillation dataset pipeline with gate filtering, top-quartile selection, family filtering, and failure-example policy.

#### Training & Distillation

- Base model selection maps scenario families to training modes (from-scratch, LoRA, and full fine-tune).
- Training backend abstraction with MLX and CUDA plus an injectable `TrainingExecutor` hook.
- Prompt alignment ensures distilled models match runtime invocation.
- Candidate-shadow-active promotion lifecycle with configurable quantitative gates and rollback.

### Changed

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

[0.3.6]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.5...py-v0.3.6
[0.3.5]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.4...py-v0.3.5
[0.3.4]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.3...py-v0.3.4
[0.3.3]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.2...py-v0.3.3
[0.3.2]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.1...py-v0.3.2
[0.3.1]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.0...py-v0.3.1
[0.3.0]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.2.4...py-v0.3.0
[0.2.4]: https://github.com/greyhaven-ai/autocontext/compare/v0.2.0...py-v0.2.4
[0.2.0]: https://github.com/greyhaven-ai/autocontext/releases/tag/v0.2.0
