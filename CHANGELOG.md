# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Session notebook context now flows into runtime prompts and cockpit views for active runs.
- World-state abstractions now support stateful scenario families and workflow-style scenarios.
- Repo-local dataset discovery and schema adaptation: scan repo trees, convert JSONL/JSON/CSV to ShareGPT training format with provenance (AC-461).
- Trace pipeline audit fixes: expanded redaction patterns, ISO 8601 timestamps, explicit role mapping, export warnings, HF format fix (AC-468).
- Privacy-aware trace export workflow: select runs → redact → validate → package with manifest and attestation (AC-463).
- Publishing connectors: local JSONL, GitHub Gist, and Hugging Face dataset publishers with trace ingestion and deduplication (AC-465).
- Trace-to-model data plane: DatasetCurator + DataPlane orchestrator for curated training datasets with provenance and held-out splits (AC-466).
- Open public trace schema v1.0.0: versioned interchange format for coding agent traces with provenance, attestation, and redaction metadata (AC-462).
- Sensitive-data detection and redaction pipeline: secrets, PII, paths, custom patterns with policy-backed actions (AC-464).
- Mission-simulation integration: missions can invoke simulations as planning tools before committing to actions (AC-455).
- Consolidated TUI: removed web dashboards and standalone tui/ package — server is API-only, single Ink TUI (AC-467).
- Richer sweep DSL: categorical sweeps, logarithmic scales, sweep file loading, and named presets (AC-454).
- Python parity for simulate: SimulationEngine with run, replay, compare, export — matching TS surface (AC-453).
- `simulate export` — portable simulation result packages in JSON, Markdown, and CSV formats (AC-452).
- `simulate compare` — structured diff between simulation runs with variable and dimension deltas (AC-451).
- `simulate replay` — re-execute saved simulations with optional variable overrides and comparison data (AC-450).
- First-class `analyze` command for comparing runs, missions, simulations, and generated artifacts (AC-448).
- First-class `investigate` command for plain-language diagnosis, evidence gathering, and root-cause analysis (AC-447).
- First-class `simulate` command for plain-language multi-variable simulation, sweeps, and analysis (AC-446).
- Campaign abstraction for coordinating multiple missions under long-term goals (AC-428).
- Scenario parity matrix documenting Python/TypeScript surface coverage, creation flows, runtime support, and explicit limitations (AC-431).

### Fixed
- TS `detectScenarioFamily` now delegates to the full weighted classifier instead of naive keyword matching, so all custom-scenario-supported families are reachable through CLI without auto-routing into unsupported custom game creation (AC-437).
- TS `new-scenario` CLI now materializes runnable artifacts to disk instead of only echoing specs (AC-433).
- TS adaptive mission execution: missions now decompose plain-language goals into subgoals and plan steps via LLM instead of generic bookkeeping (AC-435).
- TS spec auto-heal: codegen failures from malformed specs now trigger automatic recovery (missing sampleInput, type coercion, field inference) instead of hard stops (AC-440).
- TS scenario revision flow: users can refine created scenarios with feedback instead of starting over (AC-441).
- TS deep execution validation: generated scenario code is now executed and verified before registration, catching logic errors not visible to AST checks (AC-442).
- TS scenario templates: 3 pre-built templates (content-generation, prompt-optimization, rag-accuracy) for scaffolding without LLM calls (AC-443).

### Changed
- Agent-task scaffolding and execution now use separate phased budgets.
- Operator-loop scenarios remain available as typed family metadata, but executable operator-loop scaffolding has been removed so the harness no longer bakes in escalation-specific runtime behavior.
- `operator_loop` explicitly unsupported with test coverage confirming clear error guidance in both Python and TypeScript (AC-432).
- Public repo docs now include a docs landing page, package-selection guidance, an analytics/adoption guide, a release checklist, copy-paste integration examples for CLI, MCP, Python SDK, and TypeScript usage, plus README package/download signals.

### Fixed
- Python package fallback version metadata now matches the published `0.2.0` package version.

## [0.2.0] - 2026-03-15

### Added
- Typed scenario-family architecture with registry-driven creation, routing, validation, and persistence.
- New scenario families across Python and TypeScript:
  `simulation`, `artifact_editing`, `investigation`, `workflow`,
  `schema_evolution`, `tool_fragility`, `negotiation`,
  `operator_loop`, and `coordination`.
- Aggregate run analytics with structured facets, signal clustering, release/runtime correlation, and thresholded issue/probe generation.
- Rubric-drift monitoring and human calibration queue generation.
- Canonical run-event traces, causal inspection artifacts, and timeline/state inspection.
- Trace-grounded writeups and weakness reports backed by structured event evidence.

### Changed
- Natural-language scenario creation now routes through scenario-family inference instead of defaulting to generic agent-task scaffolds.
- Family-specific generator and validator pipelines are now the live creation path in both Python and TypeScript.
- Public cockpit writeups and run-completion weakness reporting now prefer trace-grounded artifacts when available.
- Release automation now supports trusted publishing to both PyPI and npm.

### Fixed
- Generated agent-task evaluation now uses configured runtime providers and provider-neutral model fallback behavior.
- Scenario intent validation now blocks obvious family/output mismatches before scaffolding.
- Aggregate issue/probe dedup now keys on correlated evidence scope instead of signal type alone.
- Timeline and reporting consumers now honor canonical `causal_edges` instead of relying only on inline cause IDs.

## [0.1.2] - 2026-03-15

### Added
- Initial trusted publishing flow for PyPI and npm.
