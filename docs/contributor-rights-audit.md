# Contributor Rights Audit for the Core/Control Licensing Split

This is the AC-646 rights-audit artifact for the AutoContext licensing
structure transition. It supports the package-boundary work in
[`core-control-package-split.md`](./core-control-package-split.md) and the
machine-readable boundary guardrails in
[`packages/package-boundaries.json`](../packages/package-boundaries.json).

This document is an engineering audit, not legal advice. It records what git
history can prove and identifies which business/legal records still need to be
checked before any non-Apache relicensing or AC-645 license metadata publication
can proceed.

## Current Status

- Audit snapshot: `0aa0114e` (`main`, after production-trace SDK build helper)
- License metadata status: still deferred to AC-645.
- Non-Apache relicensing status: **not finally approved yet**.
- Grey Haven confirmation received on 2026-04-28: contributions authored under
  `cirdan-greyhaven` are treated as a Grey Haven-controlled contributor identity
  for this engineering audit.
- Current blocker: Grey Haven still needs a final business/legal sign-off that
  it has authority to license/relicense the affected contributions from all
  observed Grey Haven-controlled contributor identities.
- Repository records checked: `CONTRIBUTING.md`, `.github/`, docs, and root
  license files. No CLA, DCO, copyright assignment, or contributor license
  agreement was found in-repo.
- The controlled-identity confirmation, empty current path-specific block list,
  and required final sign-offs are encoded in `packages/package-boundaries.json` under
  `licensing.rightsAudit` so CI can keep AC-645 metadata blocked while final
  authority is pending.

## Go / No-Go Summary

| Area                               | Current evidence                                                                                                                                                                 | Relicensing status                                                                                                                |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Grey Haven-controlled affected paths | Git history/blame show Jay Scambler identities and `cirdan-greyhaven` identities confirmed as Grey Haven-controlled in the current source lines for audited non-Apache candidate path groups. | **Conditionally clear** once Grey Haven records confirm authority to license/relicense those contributions. |
| Path-specific third-party blockers | No current non-Grey-Haven-controlled source-line blockers were found in the audited non-Apache candidate paths after recording the Cirdan identity confirmation. | **No current path-specific blocker** from git evidence; re-run if path ownership changes. |
| Gingiris contribution              | Git history shows one contribution touching `README.md` and `autocontext/src/autocontext/banner.py`; neither is currently in the proposed non-Apache path groups audited here.   | **Not a current non-Apache blocker**, but re-check if either path is moved into a non-Apache package or root docs are relicensed. |
| AC-645 license metadata            | Guarded by tests and `packages/package-boundaries.json`.                                                                                                                         | **Blocked** until final Grey Haven sign-off is recorded.                                                                          |

## Contributor Identities Seen in Affected Areas

| Canonical audit identity | Git author identities observed                                                      | Audit treatment                                 | Required authority evidence                                                                                  |
| ------------------------ | ------------------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `jay-scambler`           | `Jay Scambler <jayscambler@gmail.com>`, `Jay Scambler <jay@greyhaven.ai>` | Grey Haven contributor identity. | Confirm Grey Haven ownership/assignment or authorization covering both observed email identities. |
| `cirdan-greyhaven`       | `Cirdan <cirdan@greyhaven.ai>`, `Cirdan Shipwright <cirdan@greyhaven.ai>` | Grey Haven-controlled contributor identity. | Covered by final Grey Haven authority confirmation; preserve the 2026-04-28 confirmation in AC-646 records. |
| `gingiris`               | `Gingiris <iris103195@gmail.com>`                                         | No current non-Apache path impact found.        | If future path maps include the touched files, request explicit permission or keep those files Apache.       |

## Affected Path Groups Audited

The audit uses the current package/path split documents as the source of truth
for code that may move into a source-available control-plane tier. Open/core
production-trace contracts and SDK helpers are intentionally excluded unless the
path map later marks them non-Apache.

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
| `jay-scambler`     |                               75 |                        12,016 | Conditionally clear pending final Grey Haven authority confirmation.                                    |
| `cirdan-greyhaven` |                                1 |                           117 | Treated as Grey Haven-controlled contributor identity; conditionally clear with final Grey Haven sign-off. |

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
| `jay-scambler`     |                               28 |                         2,399 | Conditionally clear pending final Grey Haven authority confirmation.                                    |
| `cirdan-greyhaven` |         See blamed commits below |                           170 | Treated as Grey Haven-controlled contributor identity; conditionally clear with final Grey Haven sign-off. |

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
| `jay-scambler` |                              146 |                        32,004 | Conditionally clear pending final Grey Haven authority confirmation. |

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
| `jay-scambler` |                                4 |                         5,014 | Conditionally clear pending final Grey Haven authority confirmation. |

No non-Grey-Haven-controlled current source lines were found in this path group.

### TypeScript public-trace control candidates

Audited paths include data-plane, dataset, distillation, export, publishing,
redaction workflow, and ingest workflow files under `ts/src/traces/`. The open
public schema files are intentionally excluded from this non-Apache candidate
set.

Evidence summary:

| Contributor    | Direct path-log commits in group | Current blamed lines in group | Status                                                               |
| -------------- | -------------------------------: | ----------------------------: | -------------------------------------------------------------------- |
| `jay-scambler` |                               16 |                         2,756 | Conditionally clear pending final Grey Haven authority confirmation. |

No non-Grey-Haven-controlled current source lines were found in this path group.

### TypeScript knowledge control candidates

Audited paths include solve workflows, package workflows, skill-package
workflows, research hub, and package helper files under `ts/src/knowledge/`.
Core-leaning local runtime artifacts such as `artifact-store.ts`, `playbook.ts`,
`trajectory.ts`, and public package/skill contract files are intentionally
excluded unless the path map later assigns them to the non-Apache tier.

Evidence summary:

| Contributor        | Direct path-log commits in group | Current blamed lines in group | Status                                                                                                  |
| ------------------ | -------------------------------: | ----------------------------: | ------------------------------------------------------------------------------------------------------- |
| `jay-scambler`     |                               22 |                         2,836 | Conditionally clear pending final Grey Haven authority confirmation.                                    |
| `cirdan-greyhaven` |                                1 |                            70 | Treated as Grey Haven-controlled contributor identity; conditionally clear with final Grey Haven sign-off. |

Current files with Cirdan-identity lines:

| Path                                | Cirdan-identity lines | Representative blamed commits                           |
| ----------------------------------- | -----------------: | ------------------------------------------------------- |
| `ts/src/knowledge/skill-package.ts` |                 70 | `27d79071` skill export + agent task markdown rendering |

## Current Path-Specific Blockers

No current path-specific third-party relicensing blockers remain in the audited
non-Apache candidate paths after recording the `cirdan-greyhaven` Grey Haven
controlled-identity confirmation.

This does **not** approve non-Apache relicensing yet. AC-645 remains blocked
until final Grey Haven authority/sign-off is recorded for affected
contributions across all observed Grey Haven-controlled contributor identities.

## Required Follow-Up Before AC-645

1. Record final Grey Haven authority/ownership confirmation for affected
   contributions across all observed Grey Haven-controlled contributor identities.
2. Preserve the 2026-04-28 confirmation that `cirdan-greyhaven` contributions
   are covered by Grey Haven AI licensing authority in the AC-646 Linear/PR
   records.
3. Decide how to handle `gingiris` if root docs, banner code, or other currently
   Apache-compatible surfaces become part of a non-Apache package or path-level
   notice.
4. Re-run this audit after any substantial AC-644 path movement and before
   AC-645 adds per-package license metadata.
5. Add the final legal/business sign-off reference to this document or to the
   AC-646 Linear issue before marking AC-646 Done.

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

The audit should be regenerated if new path groups are added to the non-Apache
control-plane tier or if AC-644 physically moves source files before AC-645.
