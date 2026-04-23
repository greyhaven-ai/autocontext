# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

Changes in this section are on the branch/repo after `0.4.4` and are not part of the last published release until the next version is cut.

### Added

- Added a shared browser exploration contract and package-safe configuration surface across Python and TypeScript, including canonical schemas, validation helpers, secure `AUTOCONTEXT_BROWSER_*` defaults, and policy helpers.
- Added the TypeScript Chrome DevTools Protocol backend for browser exploration, including attach-only target discovery, websocket transport, policy-gated actions, and evidence artifacts.
- Added Python browser exploration integration for investigations and queued tasks, including policy-gated snapshot capture, prompt/evidence enrichment, and fail-closed task-runner wiring.
- Added a thin Python Chrome CDP browser backend with debugger-target discovery, evidence persistence, WebSocket transport, runtime factory, and policy-checked session actions.
- Added cross-runtime browser contract fixtures so Python and TypeScript validators stay in lockstep.
- Added TypeScript browser-context integration for investigations, queued tasks, and MCP queueing, including fail-closed navigation policy handling and artifact-backed browser evidence.

## [0.4.4] - 2026-04-20

### Added

- Added the production-traces contract and traffic-to-eval pipeline across Python and TypeScript, including cross-runtime schemas, emit/validate helpers, redaction, retention, dataset building, CLI/MCP surfaces, and golden integration flows.
- Added the TypeScript control-plane `model-routing` actuator plus the published `chooseModel` runtime helper for deterministic route, rollout, guardrail, fallback, and trace-integrated model selection.
- Added Python solve ergonomics for family overrides and improved classifier observability/fallback vocabulary for finance, schema-evolution, geopolitical simulation, and alignment-stress prompts.

### Fixed

- Hardened Python scenario design and solve paths around malformed designer responses, intent-drift retry feedback, mandatory calibration examples, structured quality thresholds, readable sample prompts, and schema/geopolitical simulate routing.
- Preserved the latest control-plane hardening while restacking the production-traces/model-routing foundation, including candidate artifact boundary validation and model-routing payload registration.

### Changed

- Python and TypeScript package metadata are bumped to `0.4.4`.

## [0.4.3] - 2026-04-17

### Fixed

- Hardened Pi-backed solve/runtime execution so Pi RPC waits for assistant completion, honors model/context-file options consistently, and solve runs enforce timeout budgets.
- Preserved generated-scenario family behavior across solve, export, TypeScript `new-scenario`, and `improve` flows, including empty-action family specs and improve calls without an initial output.
- Made custom scenario loading resilient and diagnosable: malformed specs no longer block registry discovery, spec-only directories surface actionable diagnostics, import-time missing files keep their real reason, and non-agent family specs can auto-materialize Python `scenario.py` sources.
- Normalized structured agent-task prompt payloads before validation and code generation, so JSON-like sample inputs, reference context, preparation instructions, and revision prompts no longer crash generated runtimes.

### Changed

- Python and TypeScript package metadata are bumped to `0.4.3`.

## [0.4.2] - 2026-04-16

### Fixed

- Preserved TypeScript workflow and custom-scenario semantics across broader scenario generation, including workflow compensation/side-effect metadata and camelCase final score weights.
- Hardened Python judge, improve, simulate, and list CLI flows around timeout overrides, fresh workspaces, provider overrides, rubric guardrails, and simulation-family routing.
- Added the Python `autoctx investigate` surface with generation fallbacks and kept its CLI implementation below the repository module-size gate.
- Restored Python `autoctx queue add --task-prompt ... --rubric ...` compatibility for prompt-backed queued tasks, including direct ad hoc queueing without a saved spec name.

### Changed

- Python and TypeScript package metadata are bumped to `0.4.2`.

## [0.4.1] - 2026-04-14

### Fixed

- Restored operator-loop escalation accounting when explicit escalation actions also mention clarification, so generated Python scenarios preserve both escalation and clarification signals.
- Preserved operator-loop family routing through Python solve creation and replay-safe feedback validation without violating the Pydantic serialization convention.
- Routed TypeScript `new-scenario` operator-loop requests through the dedicated family designer and allowed generated operator-loop scenarios to execute through the solve codegen path.
- Python and TypeScript package metadata are bumped to `0.4.1`.

## [0.4.0] - 2026-04-14

### Changed

- Refactored the TypeScript platform foundation, analytics/trace/training, and control-plane integration surfaces into thinner workflow modules while preserving CLI, MCP, and package parity.
- Hardened the extracted package-surface workflows around typed MCP tool boundaries, simulation dashboard report parsing, and deterministic simulation score normalization.
- Python and TypeScript package metadata are bumped to `0.4.0`.

## [0.3.7] - 2026-04-08

### Added

- TypeScript `autoctx campaign` CLI with create, status, list, add-mission, progress, pause, resume, and cancel subcommands, completing the CLI surface for CampaignManager (AC-533).
- Campaign API endpoints and MCP tools for multi-mission coordination with budget tracking and dependency graphs.

### Changed

- Standardized Anthropic credential loading around `ANTHROPIC_API_KEY` while keeping `AUTOCONTEXT_ANTHROPIC_API_KEY` as a compatibility alias across Python and TypeScript settings.
- Added optional role-scoped credential and endpoint overrides (`AUTOCONTEXT_{ROLE}_API_KEY`, `AUTOCONTEXT_{ROLE}_BASE_URL`) for `competitor`, `analyst`, `coach`, and `architect`, falling back to the global provider configuration when unset.

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

[0.4.4]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.3...py-v0.4.4
[0.4.3]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.2...py-v0.4.3
[0.4.2]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.1...py-v0.4.2
[0.4.1]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.0...py-v0.4.1
[0.4.0]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.7...py-v0.4.0
[0.3.7]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.6...py-v0.3.7
[0.3.6]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.5...py-v0.3.6
[0.3.5]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.4...py-v0.3.5
[0.3.4]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.3...py-v0.3.4
[0.3.3]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.2...py-v0.3.3
[0.3.2]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.1...py-v0.3.2
[0.3.1]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.0...py-v0.3.1
[0.3.0]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.2.4...py-v0.3.0
[0.2.4]: https://github.com/greyhaven-ai/autocontext/compare/v0.2.0...py-v0.2.4
[0.2.0]: https://github.com/greyhaven-ai/autocontext/releases/tag/v0.2.0
