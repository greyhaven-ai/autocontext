# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Session notebook context now flows into runtime prompts and cockpit views for active runs.
- World-state abstractions now support stateful scenario families and workflow-style scenarios.

### Changed
- Agent-task scaffolding and execution now use separate phased budgets.
- Public repo docs now include a docs landing page, package-selection guidance, an analytics/adoption guide, a release checklist, and copy-paste integration examples for CLI, MCP, Python SDK, and TypeScript usage.

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
