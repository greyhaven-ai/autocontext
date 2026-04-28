# Contributor Rights Audit Historical Snapshot

This is the AC-646 rights-audit historical snapshot that was created during the
abandoned dual-license investigation. It now supports provenance context for the
package-boundary work in
[`core-control-package-split.md`](./core-control-package-split.md) and the
machine-readable boundary guardrails in
[`packages/package-boundaries.json`](../packages/package-boundaries.json).

This document is an engineering audit, not legal advice. It records what git
history could prove at the time of the audit. It is no longer a go/no-go gate
for relicensing this repository: existing public repo code remains Apache-2.0,
and future proprietary work should live in a separate repo under its own
license.

## Current Status

- Audit snapshot: `0aa0114e` (`main`, after production-trace SDK build helper)
- License strategy status: existing public repo code remains Apache-2.0.
- Historical relicensing status: **out of scope**.
- Grey Haven confirmation received on 2026-04-28: contributions authored under
  `cirdan-greyhaven` are treated as a Grey Haven-controlled contributor identity
  for this engineering audit.
- Current blocker: none for boundary wrap-up. This audit would need fresh legal
  review only if the project reopens historical relicensing later.
- Repository records checked: `CONTRIBUTING.md`, `.github/`, docs, and root
  license files. No CLA, DCO, copyright assignment, or contributor license
  agreement was found in-repo.
- The controlled-identity confirmation and empty current path-specific block
  list are preserved in `packages/package-boundaries.json` under
  `licensing.rightsAudit` as historical context.

## Historical Summary

| Area                               | Current evidence                                                                                                                                                                 | Current treatment                                                                                                                |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Grey Haven-controlled affected paths | Git history/blame show Jay Scambler identities and `cirdan-greyhaven` identities confirmed as Grey Haven-controlled in the current source lines for previously audited candidate path groups. | Historical provenance context only; existing code remains Apache-2.0. |
| Path-specific third-party blockers | No current non-Grey-Haven-controlled source-line blockers were found in the audited path groups after recording the Cirdan identity confirmation. | No blocker for boundary wrap-up; re-run only if historical relicensing is reconsidered. |
| Gingiris contribution              | Git history shows one contribution touching `README.md` and `autocontext/src/autocontext/banner.py`. | Keep the touched files Apache-2.0 with the rest of the existing repo. |
| AC-645 license metadata            | Guarded by tests and `packages/package-boundaries.json`. | Superseded unless re-scoped to Apache metadata hygiene. |

## Contributor Identities Seen in Affected Areas

| Canonical audit identity | Git author identities observed                                                      | Audit treatment                                 | Required authority evidence                                                                                  |
| ------------------------ | ------------------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `jay-scambler`           | `Jay Scambler <jayscambler@gmail.com>`, `Jay Scambler <jay@greyhaven.ai>` | Grey Haven contributor identity. | Historical context only while existing code remains Apache-2.0. |
| `cirdan-greyhaven`       | `Cirdan <cirdan@greyhaven.ai>`, `Cirdan Shipwright <cirdan@greyhaven.ai>` | Grey Haven-controlled contributor identity. | Preserve the 2026-04-28 confirmation in AC-646 records. |
| `gingiris`               | `Gingiris <iris103195@gmail.com>`                                         | Outside contributor identity.        | Keep existing contributions Apache-2.0.       |

## Affected Path Groups Audited

The audit used the package/path split documents that existed at the time as the
source of truth for code that might have moved into a non-Apache control-plane
tier. This framing is now historical; current boundary work keeps the existing
repo Apache-2.0.

### Python control-plane directories

Audited paths:

- `autocontext/src/autocontext/server/`
- `autocontext/src/autocontext/mcp/`
- `autocontext/src/autocontext/monitor/`
- `autocontext/src/autocontext/notebook/`
- `autocontext/src/autocontext/openclaw/`
- `autocontext/src/autocontext/sharing/`
- `autocontext/src/autocontext/research/`
- `autocontext/src/autocontext/training/`
- `autocontext/src/autocontext/consultation/`
- `packages/python/control/`

Evidence summary:

| Contributor        | Direct path-log commits in group | Current blamed lines in group | Status                                                                                                  |
| ------------------ | -------------------------------: | ----------------------------: | ------------------------------------------------------------------------------------------------------- |
| `jay-scambler`     |                               75 |                        12,016 | Historical provenance context; existing code remains Apache-2.0.                                    |
| `cirdan-greyhaven` |                                1 |                           117 | Treated as Grey Haven-controlled contributor identity for historical context. |

Current files with Cirdan-identity lines:

| Path                                        | Cirdan-identity lines | Representative blamed commits                                                                                                           |
| ------------------------------------------- | -----------------: | --------------------------------------------------------------------------------------------------------------------------------------- |
| `autocontext/src/autocontext/mcp/server.py` |                107 | `909e0779` MCP server hardening; `0f2329e3` agent-task human feedback; `4a4135b2` MCP tool gaps; `2a38bb91` multi-step improvement loop |
| `autocontext/src/autocontext/mcp/tools.py`  |                 10 | `909e0779` MCP server hardening; `9b193391` agent task foundation; `0f2329e3` human feedback loop; `4a4135b2` MCP tool gaps             |

### Python knowledge control candidates

Audited paths:

- `autocontext/src/autocontext/knowledge/export.py`
- `autocontext/src/autocontext/knowledge/package.py`
- `autocontext/src/autocontext/knowledge/search.py`
- `autocontext/src/autocontext/knowledge/solver.py`
- `autocontext/src/autocontext/knowledge/solve_agent_task_design.py`
- `autocontext/src/autocontext/knowledge/research_hub.py`

Evidence summary:

| Contributor        | Direct path-log commits in group | Current blamed lines in group | Status                                                                                                  |
| ------------------ | -------------------------------: | ----------------------------: | ------------------------------------------------------------------------------------------------------- |
| `jay-scambler`     |                               28 |                         2,399 | Historical provenance context; existing code remains Apache-2.0.                                    |
| `cirdan-greyhaven` |         See blamed commits below |                           170 | Treated as Grey Haven-controlled contributor identity for historical context. |

Current files with Cirdan-identity lines:

| Path                                              | Cirdan-identity lines | Representative blamed commits                                                                                                                              |
| ------------------------------------------------- | -----------------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `autocontext/src/autocontext/knowledge/export.py` |                160 | `9b193391` agent task foundation; `93d8e4d3` reference context + judge enhancement; `4fdc79b0` context preparation; `2a38bb91` multi-step improvement loop |
| `autocontext/src/autocontext/knowledge/search.py` |                 10 | `9b193391` agent task foundation                                                                                                                           |

### TypeScript control-plane directories

Audited paths:

- `ts/src/control-plane/`
- `ts/src/server/`
- `ts/src/mcp/`
- `ts/src/mission/`
- `ts/src/tui/`
- `ts/src/training/`
- `ts/src/research/`
- `packages/ts/control-plane/`

Evidence summary:

| Contributor    | Direct path-log commits in group | Current blamed lines in group | Status                                                               |
| -------------- | -------------------------------: | ----------------------------: | -------------------------------------------------------------------- |
| `jay-scambler` |                              146 |                        32,004 | Historical provenance context; existing code remains Apache-2.0. |

No non-Grey-Haven-controlled current source lines were found in this path group.

### TypeScript production-trace control candidates

Audited paths:

- `ts/src/production-traces/cli/`
- `ts/src/production-traces/ingest/`
- `ts/src/production-traces/dataset/`
- `ts/src/production-traces/retention/`

Evidence summary:

| Contributor    | Direct path-log commits in group | Current blamed lines in group | Status                                                               |
| -------------- | -------------------------------: | ----------------------------: | -------------------------------------------------------------------- |
| `jay-scambler` |                                4 |                         5,014 | Historical provenance context; existing code remains Apache-2.0. |

No non-Grey-Haven-controlled current source lines were found in this path group.

### TypeScript public-trace control candidates

Audited paths include data-plane, dataset, distillation, export, publishing,
redaction workflow, and ingest workflow files under `ts/src/traces/`. The open
public schema files were excluded from the historical candidate set.

Evidence summary:

| Contributor    | Direct path-log commits in group | Current blamed lines in group | Status                                                               |
| -------------- | -------------------------------: | ----------------------------: | -------------------------------------------------------------------- |
| `jay-scambler` |                               16 |                         2,756 | Historical provenance context; existing code remains Apache-2.0. |

No non-Grey-Haven-controlled current source lines were found in this path group.

### TypeScript knowledge control candidates

Audited paths include solve workflows, package workflows, skill-package
workflows, research hub, and package helper files under `ts/src/knowledge/`.
Core-leaning local runtime artifacts such as `artifact-store.ts`, `playbook.ts`,
`trajectory.ts`, and public package/skill contract files are intentionally
excluded from this historical audit slice.

Evidence summary:

| Contributor        | Direct path-log commits in group | Current blamed lines in group | Status                                                                                                  |
| ------------------ | -------------------------------: | ----------------------------: | ------------------------------------------------------------------------------------------------------- |
| `jay-scambler`     |                               22 |                         2,836 | Historical provenance context; existing code remains Apache-2.0.                                    |
| `cirdan-greyhaven` |                                1 |                            70 | Treated as Grey Haven-controlled contributor identity for historical context. |

Current files with Cirdan-identity lines:

| Path                                | Cirdan-identity lines | Representative blamed commits                           |
| ----------------------------------- | -----------------: | ------------------------------------------------------- |
| `ts/src/knowledge/skill-package.ts` |                 70 | `27d79071` skill export + agent task markdown rendering |

## Current Path-Specific Blockers

No current path-specific third-party blocker remains relevant to the boundary
wrap-up because the existing repo is staying Apache-2.0.

This does **not** approve non-Apache relicensing. It records that historical
relicensing is out of scope. If Grey Haven later reopens historical relicensing,
this audit should be treated as stale input and rerun with legal review.

## Follow-Up

1. Preserve the 2026-04-28 confirmation that `cirdan-greyhaven` contributions
   are treated as a Grey Haven-controlled contributor identity in the AC-646
   Linear/PR records.
2. Keep existing `gingiris` contributions Apache-2.0 with the rest of the
   public repo.
3. Put future proprietary work in a separate repo under its own license rather
   than trying to reclassify historical files in this repo.

## Reproduction Commands

Contributor history by path group was generated from `git log` over the audited
paths. Current-line evidence was generated from `git blame --line-porcelain` and
canonicalized into the identity groups above.

Useful checks:

```bash
git shortlog -sne HEAD

git log --format='%H%x09%an%x09%ae%x09%aI%x09%s' -- \
  autocontext/src/autocontext/server \
  autocontext/src/autocontext/mcp \
  autocontext/src/autocontext/monitor \
  autocontext/src/autocontext/notebook \
  autocontext/src/autocontext/openclaw \
  autocontext/src/autocontext/sharing \
  autocontext/src/autocontext/research \
  autocontext/src/autocontext/training \
  autocontext/src/autocontext/consultation \
  packages/python/control

git blame --line-porcelain -- autocontext/src/autocontext/mcp/server.py
```

The audit should be regenerated only if the project reopens historical
relicensing. It is not required for Apache package-boundary wrap-up.
