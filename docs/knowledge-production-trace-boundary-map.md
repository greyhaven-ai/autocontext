# Knowledge and Production Trace Boundary Map

This document expands the mixed-domain guidance in
[`core-control-package-split.md`](./core-control-package-split.md). It is a
planning artifact for AC-650: no source files move here, no package exports
change here, and no license metadata is added here.

The purpose is to make the next extraction PRs small and test-driven. Future
PRs should turn one row of this map into failing boundary tests, then move or
facade only that row while preserving the existing compatibility surfaces.

## Non-Goals

- Do not move `knowledge` as one unit.
- Do not move all trace code as one unit.
- Do not change `autocontext`, `autoctx`, or the `autoctx` CLI compatibility
  paths while the split is in progress.
- Do not publish AC-645 license metadata or any non-Apache relicensing while
  AC-646 remains unresolved.

## Ubiquitous Language

| Term                    | Meaning for the split                                                                                                                                                                       |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Knowledge artifact      | A local artifact used by the runtime loop: playbooks, lessons, dead ends, session reports, trajectories, progress snapshots, and package metadata.                                          |
| Runtime knowledge store | File-backed local persistence needed by the open runtime to resume, score, compact, and explain runs.                                                                                       |
| Strategy package        | Portable knowledge bundle for import/export between projects or agents. Its stable wire shape can be open; orchestration around publishing/importing is control-plane.                      |
| Skill package           | Agent-facing exported strategy package. The schema can be open; export/import workflows are control-plane unless reduced to pure serialization helpers.                                     |
| Solve job               | Operator workflow that creates/selects a scenario, runs improvement, and emits a package. This is control-plane.                                                                            |
| Research hub            | Operator collaboration surface for sharing sessions, packages, results, promotions, and notebook state. This is control-plane.                                                              |
| Production trace        | Customer-side record of an LLM interaction in the production-traces contract. The contract and emit SDK should remain open.                                                                 |
| Emit SDK                | Customer-side helpers for building, hashing, validating, and writing production traces. This is open/core-safe when it has no ingestion, retention, dataset, CLI, or management dependency. |
| Ingestion pipeline      | Workflow that scans incoming traces, validates/deduplicates them, applies policy, and records receipts. This is control-plane.                                                              |
| Dataset build           | Workflow that selects, clusters, splits, curates, or promotes trace-derived training/evaluation datasets. This is control-plane.                                                            |
| Public trace            | Open interchange trace format for sharing run artifacts across harnesses. The schema is open; publishing/data-plane workflows are control-plane.                                            |

## Bounded Contexts

### Open Runtime / Core

Owns deterministic local runtime behavior and public interchange contracts:

- prompt/context compaction and knowledge scoring helpers needed by the loop;
- local artifact stores needed to resume runs and render runtime reports;
- stable knowledge/package wire types when they are pure data contracts;
- production-trace schemas, branded IDs, validators, taxonomy, and emit SDK;
- public-trace schemas and pure conversion helpers.

Core must not own operator orchestration, management APIs, MCP tools, server
routes, publishing workflows, or dataset/retention operations.

### Control Plane

Owns operator workflows and management surfaces:

- solve orchestration and generated scenario workflows;
- knowledge search, import/export, skill/package publication, and research hub;
- API, server, MCP, and CLI surfaces that expose knowledge operations;
- production trace ingestion, retention, dataset construction, CLI commands,
  policy management, export/publishing, and promotion workflows;
- management UX or registry concepts around emitted traces.

Control-plane code may depend on core contracts and SDK helpers. Core code must
not depend on control-plane code.

### Proprietary / Deferred Cloud + Box

Keep these out of Apache/core and source-available control-plane artifacts until
the product boundary is explicitly implemented:

- hosted trace warehouse, cross-tenant registry, or fleet retention service;
- enterprise-only dataset marketplace, promotion approval UI, or policy center;
- managed knowledge sharing across organizations;
- Cloud/Box deployment automation and hosted control-plane infrastructure.

These are not AC-645 license metadata. They are future product placement notes.

## Knowledge Split Map

### Python Knowledge

| Surface                                | Current path                                                                                                 | Proposed owner                                                  | Boundary rule                                                                        |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Coherence checks                       | `autocontext/src/autocontext/knowledge/coherence.py`                                                         | Core/open runtime                                               | Pure consistency checks may move with loop/runtime support.                          |
| Prompt compaction                      | `autocontext/src/autocontext/knowledge/compaction.py`                                                        | Core/open runtime                                               | Keep available to prompts/session/runtime; no server/MCP dependencies.               |
| Dead-end consolidation                 | `autocontext/src/autocontext/knowledge/dead_end_manager.py`                                                  | Core/open runtime                                               | Local run artifact logic; preserve old import path as compatibility shim.            |
| Evidence freshness and hint volume     | `evidence_freshness.py`, `hint_volume.py`                                                                    | Core/open runtime                                               | Runtime context quality controls; no operator workflow dependencies.                 |
| Local knowledge state                  | `lessons.py`, `mutation_log.py`, `progress.py`, `report.py`, `trajectory.py`, `stagnation.py`, `weakness.py` | Core/open runtime                                               | Local persistence/reporting contracts used by the improvement loop.                  |
| Runtime gates and tuning value objects | `protocol.py`, `rapid_gate.py`, `tuning.py`                                                                  | Core/open runtime if kept as deterministic value/rule objects   | Keep only pure domain rules in core; workflow orchestration stays outside.           |
| Harness metrics                        | `harness_quality.py`, `normalized_metrics.py`                                                                | Core/open runtime, pending harness extraction                   | Allowed only if dependency direction remains harness/storage -> core-safe contracts. |
| Semantic compaction benchmark          | `semantic_compaction_benchmark.py`                                                                           | Defer / core-adjacent                                           | Do not extract until benchmark/observability ownership is explicit.                  |
| Fresh start workflow                   | `fresh_start.py`                                                                                             | Core/open only if reduced to local artifact operation           | If it becomes operator-driven orchestration, keep in control-plane.                  |
| Strategy/skill export                  | `export.py`                                                                                                  | Control-plane workflow with open data contracts                 | May depend on core package types; should not be imported by core.                    |
| Strategy package import/export         | `package.py`                                                                                                 | Mixed: data contract open, import/export workflow control-plane | Split wire schema from filesystem import/publish workflow before moving.             |
| Knowledge search                       | `search.py`                                                                                                  | Control-plane                                                   | Operator/MCP/server readback surface.                                                |
| Solve orchestration                    | `solver.py`, `solve_agent_task_design.py`                                                                    | Control-plane                                                   | Creates/runs scenarios and exports packages; never core.                             |
| Research hub                           | `research_hub.py`                                                                                            | Control-plane                                                   | Collaboration, promotion, and sharing surface.                                       |
| Compatibility namespace                | `autocontext.knowledge.*`                                                                                    | Umbrella compatibility                                          | Preserve until downstream migration is documented.                                   |

### TypeScript Knowledge

| Surface                                     | Current path                                                                                                                 | Proposed owner                                       | Boundary rule                                                                                |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Local artifact store                        | `ts/src/knowledge/artifact-store.ts`                                                                                         | Core/open runtime                                    | Required by loop/execution and training export types; keep free of server/MCP/CLI.           |
| Versioned local files and scenario IDs      | `versioned-store.ts`, `scenario-id.ts`                                                                                       | Core/open runtime                                    | Small value/storage helpers can move early with compatibility re-exports.                    |
| Playbooks, dead ends, reports, trajectories | `playbook.ts`, `dead-end.ts`, `session-report.ts`, `trajectory.ts`                                                           | Core/open runtime                                    | Runtime knowledge artifacts used by generation loop.                                         |
| Harness snapshots                           | `harness-store.ts`                                                                                                           | Core/open if treated as local artifact persistence   | Keep package/export publication out of this layer.                                           |
| Solve budget value object                   | `solve-generation-budget.ts`                                                                                                 | Core/open if pure budget rule                        | Keep solve orchestration in control-plane.                                                   |
| Package/skill contracts                     | `package-types.ts`, `skill-package-contracts.ts`                                                                             | Open contract candidate                              | Only stable wire shapes; no filesystem publication or operator workflow.                     |
| Strategy package workflow                   | `package.ts`, `package-*` helpers                                                                                            | Control-plane workflow                               | Import/export and conflict handling are control-plane. Extract contracts first if needed.    |
| Skill package workflow                      | `skill-package*.ts`                                                                                                          | Mixed: contract open, export workflows control-plane | Split schema/types from markdown/dict/export workflows before moving.                        |
| Solve workflows                             | `solver.ts`, `solve-*.ts`, `agent-task-solve-execution.ts`, `built-in-game-solve-execution.ts`, `codegen-solve-execution.ts` | Control-plane                                        | Operator scenario creation/evolution and package emission.                                   |
| Research hub                                | `research-hub.ts`                                                                                                            | Control-plane                                        | Uses store/notebook/session/promotion concepts.                                              |
| Barrel export                               | `ts/src/knowledge/index.ts`                                                                                                  | Umbrella compatibility during migration              | Replace with package-owned exports only after sub-surfaces have owners.                      |
| API/MCP/CLI consumers                       | `ts/src/server/knowledge-api.ts`, `ts/src/mcp/*knowledge*`, `ts/src/cli/index.ts` knowledge commands                         | Control-plane                                        | Should eventually import from `@autocontext/control-plane` or compatibility shims, not core. |

## Production Trace Split Map

### Python Production Traces

| Surface                                      | Current path                                                       | Proposed owner            | Boundary rule                                                                       |
| -------------------------------------------- | ------------------------------------------------------------------ | ------------------------- | ----------------------------------------------------------------------------------- |
| Pydantic contract models                     | `autocontext/src/autocontext/production_traces/contract/models.py` | Core/open SDK             | Public customer-side schema projection.                                             |
| Branded IDs                                  | `contract/branded_ids.py`                                          | Core/open SDK             | Pure value constraints.                                                             |
| JSON schemas                                 | `contract/json_schemas/*.schema.json`                              | Core/open contract        | Authoritative wire format. Keep synchronized with TypeScript schemas.               |
| Emit helpers                                 | `emit.py`                                                          | Core/open SDK             | Customer-side trace builder/writer with no ingestion/retention/dataset dependency.  |
| Hashing and install salt                     | `hashing.py`                                                       | Core/open SDK             | Customer-side privacy primitive. Rotation command surfaces belong in control-plane. |
| Validation                                   | `validate.py`                                                      | Core/open SDK             | Pure validation helper.                                                             |
| Provider taxonomy                            | `taxonomy/*.py`                                                    | Core/open SDK             | Shared error/outcome vocabulary used by integrations.                               |
| Integration trace builders                   | `integrations/*/_trace_builder.py`                                 | Core-adjacent integration | May depend on open SDK only; no management workflow dependency.                     |
| Future ingestion/retention/dataset workflows | not yet present in Python package                                  | Control-plane             | Do not add to core package when ported.                                             |

### TypeScript Production Traces and Public Traces

The first source-ownership slice claims `ts/src/production-traces/contract/generated-types.ts`
for the TypeScript core package because it is generated from the public
production-trace JSON schemas and has no CLI, ingestion, dataset, retention,
server, MCP, or control-plane dependencies.

The next independent source-ownership slice claims `ts/src/production-traces/taxonomy/**`
for the TypeScript core package because it is shared provider error/outcome
vocabulary and does not depend on branded IDs, emit SDK helpers, CLI workflows,
ingestion, dataset generation, retention, or `ts/src/traces` workflows.

| Surface                               | Current path                                                                                            | Proposed owner                 | Boundary rule                                                                                          |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------ |
| Production trace contract             | `ts/src/production-traces/contract/**`                                                                  | Core/open SDK                  | Public wire format, branded IDs, validators, generated types.                                          |
| Customer emit SDK                     | `ts/src/production-traces/sdk/**`                                                                       | Core/open SDK                  | Preserve `autoctx/production-traces` style surface; keep tree-shakable and management-free.            |
| Taxonomy                              | `ts/src/production-traces/taxonomy/**`                                                                  | Core/open SDK                  | Shared provider error/outcome vocabulary.                                                              |
| Redaction primitives                  | `ts/src/production-traces/redaction/types.ts`, `policy.ts`, `hash-primitives.ts`, `apply.ts`, `mark.ts` | Open SDK if pure               | Keep pure local privacy helpers open; CLI policy management stays control-plane.                       |
| Ingestion                             | `ts/src/production-traces/ingest/**`                                                                    | Control-plane                  | Scans incoming traces, locks, dedupes, validates receipts.                                             |
| Retention                             | `ts/src/production-traces/retention/**`                                                                 | Control-plane                  | Project/fleet policy enforcement and GC logs.                                                          |
| Dataset generation                    | `ts/src/production-traces/dataset/**`                                                                   | Control-plane                  | Selection, clustering, splitting, manifests, and provenance workflows.                                 |
| Production traces CLI                 | `ts/src/production-traces/cli/**`                                                                       | Control-plane                  | `autoctx production-traces ...` command implementation; keep umbrella CLI compatibility.               |
| Production traces barrel              | `ts/src/production-traces/index.ts`                                                                     | Umbrella compatibility / mixed | Do not move as one unit; split subpath ownership first.                                                |
| Public trace schema                   | `ts/src/traces/public-schema*.ts`                                                                       | Core/open contract             | Open interchange schema and pure factories.                                                            |
| Public trace conversion               | `ts/src/traces/public-trace-export-workflow.ts`                                                         | Core/open if pure conversion   | If it reads/writes run artifacts or manages consent workflow, keep the orchestration in control-plane. |
| Trace redaction detector/policy       | `ts/src/traces/redaction*.ts`                                                                           | Mixed                          | Pure detection/policy can be open; export/publishing workflow is control-plane.                        |
| Export/publishing workflows           | `ts/src/traces/export-*.ts`, `publishing-workflow.ts`, `publishers*.ts`                                 | Control-plane                  | Consent, packaging, redistribution, and publishing orchestration.                                      |
| Data plane / distillation / discovery | `ts/src/traces/data-plane*`, `dataset-*`, `distillation-*`, `trace-ingest-workflow.ts`                  | Control-plane                  | Dataset and model-training pipelines.                                                                  |
| MCP/CLI production traces tools       | `ts/src/mcp/production-traces-tools.ts`, `ts/src/cli/**production-traces**`                             | Control-plane                  | Management surface over trace workflows.                                                               |

## Compatibility Paths to Preserve

| Existing surface                    | During split                                                               | After package ownership stabilizes                                                              |
| ----------------------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `autocontext.knowledge.*`           | Re-export or delegate to package-owned modules.                            | Document migration to core/control packages without breaking old imports immediately.           |
| `autocontext.production_traces.*`   | Continue to expose the customer-side SDK while package extraction happens. | Keep as compatibility wrapper around the open SDK artifact.                                     |
| `autoctx` package root              | Keep umbrella exports for current users.                                   | Narrow root exports after subpath/package migrations are documented.                            |
| `autoctx/production-traces` subpath | Preserve the customer emit SDK stability promise.                          | Back it with the open SDK package; do not point it at control-plane CLI/workflows.              |
| `autoctx production-traces ...` CLI | Keep command working from umbrella CLI.                                    | Route implementation through the control-plane artifact once available.                         |
| Server/MCP knowledge APIs           | Keep endpoints/tools stable.                                               | Route through control-plane package facades; core should expose only local artifacts/contracts. |

## Future Test Guardrails

Future extraction PRs should add RED tests before moving code. Suggested test
families:

1. **Knowledge owner manifest** — extend `packages/package-boundaries.json` with
   `mixedDomains.knowledge` rows for open contracts, core runtime helpers,
   control workflows, and deferred surfaces.
2. **Core package source scope** — assert `@autocontext/core` and
   `autocontext-core` include only explicitly allowed knowledge runtime files,
   not `solver`, `research_hub`, `search`, `package` workflows, server, MCP, or
   CLI paths.
3. **Control package source scope** — assert control-plane knowledge facades add
   package-boundary manifest entries when they import solve/package/search/hub
   workflows.
4. **Production trace SDK isolation** — assert open SDK artifacts compile without
   `production-traces/cli`, `ingest`, `dataset`, `retention`, `ts/src/traces` data
   plane, server, MCP, or umbrella CLI imports.
5. **Schema parity** — keep Python and TypeScript production-trace schemas in
   lockstep and fail if one side adds a public contract field without the other.
6. **Compatibility smoke tests** — keep `autocontext.production_traces`,
   `autocontext.knowledge`, `autoctx`, `autoctx/production-traces`, and
   `autoctx production-traces` working while internals move.
7. **No premature licensing publication** — reuse the AC-645/AC-646 guardrail;
   extraction PRs must not add license metadata.

## Recommended Extraction Order

1. Production trace contract and emit SDK package ownership. This is the cleanest
   boundary: schemas, branded IDs, validation, hashing, taxonomy, and emit
   helpers already have clear customer-side semantics.
2. TypeScript production-traces control workflows. Move or facade CLI, ingest,
   dataset, retention, and policy workflows behind the control-plane package.
3. Python knowledge runtime helpers. Start with deterministic helpers used by the
   loop: compaction, dead ends, reports, trajectories, lessons, and progress.
4. TypeScript knowledge runtime helpers. Move small storage/value helpers before
   moving any solve/package workflow.
5. Knowledge package contracts. Split stable package/skill schemas from
   import/export/publish workflows.
6. Knowledge control workflows. Move solve, search, package import/export, skill
   export, and research hub facades into the control-plane artifact.
7. Public trace export/data-plane workflows. Keep schemas open; move publishing,
   distillation, and dataset workflows to control-plane.

Each step should be a small PR with one manifest change, one RED boundary test,
one GREEN extraction/facade change, and compatibility smoke coverage.
