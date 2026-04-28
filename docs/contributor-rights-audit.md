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
- Non-Apache relicensing status: **not approved yet**.
- Current blocker: contributor authority must be confirmed for the non-Jay lines
  listed below, and Jay's own company/IP authority should be recorded in the
  final sign-off packet.
- Repository records checked: `CONTRIBUTING.md`, `.github/`, docs, and root
  license files. No CLA, DCO, copyright assignment, or contributor license
  agreement was found in-repo.
- The currently blocked paths are also encoded in
  `packages/package-boundaries.json` under `licensing.rightsAudit` so CI can
  keep AC-645 metadata blocked while authority is unclear.

## Go / No-Go Summary

| Area                             | Current evidence                                                                                                                                                               | Relicensing status                                                                                                                                   |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Jay-only affected paths          | Git history/blame show only Jay Scambler aliases in the current source lines for those path groups.                                                                            | **Conditionally clear** once Grey Haven records confirm Jay's authority to license/relicense those contributions.                                    |
| Affected paths with Cirdan lines | Current line blame shows Cirdan/Cirdan Shipwright authored lines in Python MCP, Python knowledge control candidates, and TypeScript skill-package code.                        | **Blocked / unclear** until employment, contractor, assignment, or explicit permission records confirm Grey Haven can relicense those contributions. |
| Gingiris contribution            | Git history shows one contribution touching `README.md` and `autocontext/src/autocontext/banner.py`; neither is currently in the proposed non-Apache path groups audited here. | **Not a current non-Apache blocker**, but re-check if either path is moved into a non-Apache package or root docs are relicensed.                    |
| AC-645 license metadata          | Guarded by tests and `packages/package-boundaries.json`.                                                                                                                       | **Blocked** until this audit is complete and reviewed.                                                                                               |

## Contributor Identities Seen in Affected Areas

| Canonical audit identity | Git aliases observed                                                      | Required authority evidence                                                                                                                          |
| ------------------------ | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `jay-scambler`           | `Jay Scambler <jayscambler@gmail.com>`, `Jay Scambler <jay@greyhaven.ai>` | Confirm Grey Haven ownership/assignment or maintainer authorization covering both aliases.                                                           |
| `cirdan-greyhaven`       | `Cirdan <cirdan@greyhaven.ai>`, `Cirdan Shipwright <cirdan@greyhaven.ai>` | Confirm employment/contractor IP assignment, CLA, DCO-equivalent, or explicit relicensing permission for the commits and current lines listed below. |
| `gingiris`               | `Gingiris <iris103195@gmail.com>`                                         | No current non-Apache path impact found. If future path maps include the touched files, request explicit permission or keep those files Apache.      |

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

| Contributor        | Direct path-log commits in group | Current blamed lines in group | Status                                                       |
| ------------------ | -----------------------: | ----------------------------: | ------------------------------------------------------------ |
| `jay-scambler`     |                       75 |                        12,016 | Conditionally clear pending internal authority confirmation. |
| `cirdan-greyhaven` |                        1 |                           117 | Blocked/unclear pending contributor-rights confirmation.     |

Current files with non-Jay lines:

| Path                                        | Cirdan lines | Representative blamed commits                                                                                                           |
| ------------------------------------------- | -----------: | --------------------------------------------------------------------------------------------------------------------------------------- |
| `autocontext/src/autocontext/mcp/server.py` |          107 | `909e0779` MCP server hardening; `0f2329e3` agent-task human feedback; `4a4135b2` MCP tool gaps; `2a38bb91` multi-step improvement loop |
| `autocontext/src/autocontext/mcp/tools.py`  |           10 | `909e0779` MCP server hardening; `9b193391` agent task foundation; `0f2329e3` human feedback loop; `4a4135b2` MCP tool gaps             |

### Python knowledge control candidates

Audited paths:

- `autocontext/src/autocontext/knowledge/export.py`
- `autocontext/src/autocontext/knowledge/package.py`
- `autocontext/src/autocontext/knowledge/search.py`
- `autocontext/src/autocontext/knowledge/solver.py`
- `autocontext/src/autocontext/knowledge/solve_agent_task_design.py`
- `autocontext/src/autocontext/knowledge/research_hub.py`

Evidence summary:

| Contributor        | Direct path-log commits in group | Current blamed lines in group | Status                                                       |
| ------------------ | -----------------------: | ----------------------------: | ------------------------------------------------------------ |
| `jay-scambler`     |                       28 |                         2,399 | Conditionally clear pending internal authority confirmation. |
| `cirdan-greyhaven` | See blamed commits below |                           170 | Blocked/unclear pending contributor-rights confirmation.     |

Current files with non-Jay lines:

| Path                                              | Cirdan lines | Representative blamed commits                                                                                                                              |
| ------------------------------------------------- | -----------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `autocontext/src/autocontext/knowledge/export.py` |          160 | `9b193391` agent task foundation; `93d8e4d3` reference context + judge enhancement; `4fdc79b0` context preparation; `2a38bb91` multi-step improvement loop |
| `autocontext/src/autocontext/knowledge/search.py` |           10 | `9b193391` agent task foundation                                                                                                                           |

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

| Contributor    | Direct path-log commits in group | Current blamed lines in group | Status                                                       |
| -------------- | -----------------------: | ----------------------------: | ------------------------------------------------------------ |
| `jay-scambler` |                      146 |                        32,004 | Conditionally clear pending internal authority confirmation. |

No non-Jay current source lines were found in this path group.

### TypeScript production-trace control candidates

Audited paths:

- `ts/src/production-traces/cli/`
- `ts/src/production-traces/ingest/`
- `ts/src/production-traces/dataset/`
- `ts/src/production-traces/retention/`

Evidence summary:

| Contributor    | Direct path-log commits in group | Current blamed lines in group | Status                                                       |
| -------------- | -----------------------: | ----------------------------: | ------------------------------------------------------------ |
| `jay-scambler` |                        4 |                         5,014 | Conditionally clear pending internal authority confirmation. |

No non-Jay current source lines were found in this path group.

### TypeScript public-trace control candidates

Audited paths include data-plane, dataset, distillation, export, publishing,
redaction workflow, and ingest workflow files under `ts/src/traces/`. The open
public schema files are intentionally excluded from this non-Apache candidate
set.

Evidence summary:

| Contributor    | Direct path-log commits in group | Current blamed lines in group | Status                                                       |
| -------------- | -----------------------: | ----------------------------: | ------------------------------------------------------------ |
| `jay-scambler` |                       16 |                         2,756 | Conditionally clear pending internal authority confirmation. |

No non-Jay current source lines were found in this path group.

### TypeScript knowledge control candidates

Audited paths include solve workflows, package workflows, skill-package
workflows, research hub, and package helper files under `ts/src/knowledge/`.
Core-leaning local runtime artifacts such as `artifact-store.ts`, `playbook.ts`,
`trajectory.ts`, and public package/skill contract files are intentionally
excluded unless the path map later assigns them to the non-Apache tier.

Evidence summary:

| Contributor        | Direct path-log commits in group | Current blamed lines in group | Status                                                       |
| ------------------ | -----------------------: | ----------------------------: | ------------------------------------------------------------ |
| `jay-scambler`     |                       22 |                         2,836 | Conditionally clear pending internal authority confirmation. |
| `cirdan-greyhaven` |                        1 |                            70 | Blocked/unclear pending contributor-rights confirmation.     |

Current files with non-Jay lines:

| Path                                | Cirdan lines | Representative blamed commits                           |
| ----------------------------------- | -----------: | ------------------------------------------------------- |
| `ts/src/knowledge/skill-package.ts` |           70 | `27d79071` skill export + agent task markdown rendering |

## Paths That Must Not Be Relicensed Yet

Until the required Cirdan authority evidence is found, one of these actions is
required before moving the following files to a non-Apache tier:

1. obtain and record explicit permission or assignment coverage;
2. keep the file/path Apache; or
3. rewrite/remove the affected contribution before relicensing.

Blocked/unclear current paths:

- `autocontext/src/autocontext/mcp/server.py`
- `autocontext/src/autocontext/mcp/tools.py`
- `autocontext/src/autocontext/knowledge/export.py`
- `autocontext/src/autocontext/knowledge/search.py`
- `ts/src/knowledge/skill-package.ts`

## Required Follow-Up Before AC-645

1. Confirm whether Grey Haven has employment, contractor, assignment, CLA, or
   explicit relicensing records for `cirdan-greyhaven` covering the commits
   listed above.
2. Record Jay Scambler's Grey Haven authority/ownership confirmation for both
   observed author emails.
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
