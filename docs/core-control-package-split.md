# Core/Control Package Split

This document is the source of truth for the AutoContext core/control package
boundary. It turns the Linear strategy in AC-642, AC-643, AC-644, AC-648,
AC-649, and AC-650 into a concrete implementation guardrail before moving
behavior or changing public install paths.

## Strategy

AutoContext is keeping the existing public repository and already-written code
Apache-2.0. The boundary work continues as architecture and package hygiene, not
as a historical relicensing project.

The package split should make these domains clear:

1. Apache-2.0 core: foundational runtime, SDK, scenario contracts, providers,
   execution primitives, local state, and extension points.
2. Apache-2.0 control plane: operator workflows, management UX, orchestration,
   advanced trace management, knowledge packaging/export, and other higher-level
   control surfaces that still live in this repo.
3. Future proprietary products: hosted infrastructure, enterprise deployment,
   service-only features, and other net-new proprietary work in a separate repo
   under its own license.

The goal is not a repo-wide source-available license flip. The goal is a clean
Apache public foundation with stable contracts that a future proprietary repo can
depend on without copying or relicensing historical code.

## Hard Guardrails

- Keep the existing public repository and already-written code Apache-2.0.
- Do not add dual-license metadata, per-package non-Apache license files, or a
  root `LICENSING.md` for the existing repo.
- Treat AC-645 as superseded unless it is re-scoped to Apache metadata hygiene.
- Treat AC-646 as provenance context, not as a blocker for boundary wrap-up.
- Preserve `pip install autocontext`, `npm install autoctx`, and the `autoctx`
  CLI as the default compatibility path while the split is in progress.
- Keep `autocontext/` and `ts/` as umbrella compatibility packages until the
  new artifacts are buildable and downstream migration is documented.
- Treat `knowledge` and production traces as dedicated split projects, not
  incidental fallout from package extraction.
- Prefer compatibility shims and re-exports over breaking old import paths
  during the first migration phases.

The boundary-enforcement contract also encodes the Apache-only publication rule:
no root `LICENSING.md`, no per-package non-Apache `LICENSE` files, and no
dual-license metadata for the existing repo. The AC-646 engineering audit is
preserved as historical provenance context in
[`contributor-rights-audit.md`](./contributor-rights-audit.md).

## Package Topology

The machine-readable topology map lives in
[`packages/package-topology.json`](../packages/package-topology.json). The
machine-readable boundary-enforcement contract lives in
[`packages/package-boundaries.json`](../packages/package-boundaries.json) and is
checked in CI.

| Ecosystem  | Umbrella package                                | Apache core artifact | Control-plane artifact       |
| ---------- | ----------------------------------------------- | -------------------- | ---------------------------- |
| Python     | `autocontext`                                   | `autocontext-core`   | `autocontext-control`        |
| TypeScript | `autoctx`                                       | `@autocontext/core`  | `@autocontext/control-plane` |
| Pi         | `pi-autocontext` initially depends on `autoctx` | Deferred             | Deferred                     |

The umbrella packages preserve the default install and CLI experience. The new
core/control artifacts make the dependency boundary explicit at the artifact
level while remaining Apache-2.0 in this repo.

## Path Map

This map is the starting point for implementation. It should be updated if code
review discovers a boundary mistake.

### Python Core Candidates

- `autocontext/src/autocontext/agents/`
- `autocontext/src/autocontext/analytics/`
- `autocontext/src/autocontext/agentos/`
- `autocontext/src/autocontext/blobstore/`
- `autocontext/src/autocontext/config/`
- `autocontext/src/autocontext/evaluation/`
- `autocontext/src/autocontext/evidence/`
- `autocontext/src/autocontext/execution/`
- `autocontext/src/autocontext/harness/`
- `autocontext/src/autocontext/investigation/`
- `autocontext/src/autocontext/loop/`
- `autocontext/src/autocontext/notifications/`
- `autocontext/src/autocontext/prompts/`
- `autocontext/src/autocontext/providers/`
- `autocontext/src/autocontext/runtimes/`
- `autocontext/src/autocontext/scenarios/`
- `autocontext/src/autocontext/security/`
- `autocontext/src/autocontext/session/`
- `autocontext/src/autocontext/simulation/`
- `autocontext/src/autocontext/storage/`
- `autocontext/src/autocontext/util/`

### Python Control-Plane Candidates

- `autocontext/src/autocontext/server/`
- `autocontext/src/autocontext/mcp/`
- `autocontext/src/autocontext/monitor/`
- `autocontext/src/autocontext/notebook/`
- `autocontext/src/autocontext/openclaw/`
- `autocontext/src/autocontext/sharing/`
- `autocontext/src/autocontext/research/`
- `autocontext/src/autocontext/training/`
- control-plane portions of `autocontext/src/autocontext/production_traces/`
- control-plane portions of `autocontext/src/autocontext/knowledge/`
- likely `autocontext/src/autocontext/consultation/`

### TypeScript Core Candidates

- `ts/src/agents/`
- `ts/src/analytics/`
- `ts/src/agentos/`
- `ts/src/blobstore/`
- `ts/src/config/`
- `ts/src/execution/`
- `ts/src/investigation/`
- `ts/src/judge/`
- `ts/src/loop/`
- `ts/src/prompts/`
- `ts/src/providers/`
- `ts/src/runtimes/`
- `ts/src/scenarios/`
- `ts/src/session/`
- `ts/src/simulation/`
- `ts/src/storage/`
- `ts/src/types/`
- open/shared pieces of `ts/src/traces/` and `ts/src/production-traces/`

`ts/src/runtimes/workspace-env.ts` is the first explicit runtime carve-out in
the TypeScript core artifact: it is a pure workspace/session environment
contract plus local/in-memory adapters and scoped command grants. Provider
wrappers such as Claude CLI, Codex CLI, Pi, and direct API runtimes remain
outside the core package boundary unless they are split into pure contracts and
provider-specific implementations.

### TypeScript Control-Plane Candidates

- `ts/src/control-plane/`
- `ts/src/server/`
- `ts/src/mcp/`
- `ts/src/mission/`
- `ts/src/tui/`
- `ts/src/training/`
- `ts/src/research/`
- control-plane portions of `ts/src/production-traces/`
- control-plane portions of `ts/src/knowledge/`

## Mixed Domains

The detailed planning map for knowledge and trace ownership lives in
[`knowledge-production-trace-boundary-map.md`](./knowledge-production-trace-boundary-map.md).

### Knowledge

Do not move `knowledge` as one unit.

Python core-leaning files:

- `coherence.py`
- `compaction.py`
- `dead_end_manager.py`
- `evidence_freshness.py`
- `fresh_start.py`
- `harness_quality.py`
- `hint_volume.py`
- `lessons.py`
- `mutation_log.py`
- `normalized_metrics.py`
- `progress.py`
- `protocol.py`
- `rapid_gate.py`
- `report.py`
- `stagnation.py`
- `trajectory.py`
- `tuning.py`
- `weakness.py`

Python control-leaning files:

- `export.py`
- `package.py`
- `search.py`
- `solver.py`
- `research_hub.py`

TypeScript core-leaning files:

- `artifact-store.ts`
- `dead-end.ts`
- `playbook.ts`
- `session-report.ts`
- `trajectory.ts`
- minimal runtime persistence helpers needed by loop/execution

TypeScript control-leaning files:

- `package.ts`
- package/export workflow helpers
- `solver.ts`
- `solve-*` workflows
- skill/package workflows intended for operator-facing export/import flows

### Production Traces

Keep open where possible:

- public schemas and contracts
- taxonomy and validation contracts
- SDK surfaces intended for ecosystem use

Move to the control plane:

- ingestion workflows
- retention workflows
- dataset/build/promotion pipelines
- operator registry and emit management surfaces

## Sequencing

1. PR0: land this guardrail document and topology map.
2. PR1: introduce package skeletons without moving source-of-truth behavior.
3. Create compatibility facades in domain batches, not one-symbol PRs unless a
   contract drift needs isolated review.
4. Begin real TypeScript and Python core extraction with exact file/package
   build scopes.
5. Move obvious control-plane directories.
6. Split `knowledge` deliberately.
7. Split production trace contracts/SDK from management workflows.
8. Rewire umbrella packages and CLI ownership.
9. Remove or reword any user-facing dual-license migration language before
   publishing the package split.
10. Revisit Pi dependency ownership after the TypeScript split stabilizes.

## Review Checks

- Core package builds must not compile or ship control-plane-only code.
- Core packages must not depend on control-plane artifacts or umbrella
  compatibility packages.
- Control-plane package builds may depend on core, but core must not depend on
  control-plane artifacts.
- Control-plane package facades must update the boundary manifest when they add
  source imports or TypeScript build includes.
- Broad package globs should be treated suspiciously during the split; prefer
  exact includes until ownership is settled.
- Any PR that changes existing protocol or payload semantics should say so
  explicitly instead of presenting itself as facade-only work.
- Public docs should not advertise a dual-license migration for the existing
  repo. They should describe Apache package boundaries and any future
  proprietary work as separate-repo work.
