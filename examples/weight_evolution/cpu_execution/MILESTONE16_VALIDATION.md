# Milestone 16 validation

Implemented in the isolated `codex/cpu-measured-search` worktree from merged
M15, `062addf1555146bb906eace5437840d6845b8a34`. GPU execution remains deferred.
The machine-readable evidence summary is in
[MILESTONE16_RESULTS.json](MILESTONE16_RESULTS.json).

## Implementation

The execution contract separates the native workflow design and implementation,
new adapter implementation, native worker runtime, measured process profile,
v2 failure contract and exact five-meter capacity. The synthetic
[golden fixture](../../../autocontext/tests/fixtures/weight_reproduction/cpu_execution_outcomes.json) pins
claim/outcome bindings, failed inference, non-inference exceptions and a returned
proposal followed by a rejected validation check. Unknown dispatch does not
become a false claim that no model executed.

New journals adapt the existing search and exhaustive-reference workflows without
editing their sources. Validated native receipts still drive the original policy,
scorer, selection, restoration, confirmation and conditional audit. Per-operation
outcomes precede callback checks and terminal results. Returned process evidence
survives a rejected postcheck; boundary-return status and validation status remain
separate. Non-inference errors retain v2 exception evidence with no invented
process measurements.

Exact operation inputs and charges are checked before side effects. Source and
runtime pins and checkpoint seals are checked before new work. A retained claim
never repeats its operation: saved outcomes can recover publication, while a
missing outcome remains uncertain and charged. Reports verify complete, failed
and uncertain prefixes without writes. Bounded publication scratch is retained
without treating staged-only bytes as an outcome. Current validation failures
during replay cannot be recast as historical failure observations.

References verify all three completed policy searches, their frozen selections,
practice inputs and existing checkpoint stores. They cannot materialize new
candidates, select again or receive held-out cases. Their native workflow pin is
distinct from the M16 reference-plan builder pin.

The campaign declares 18 searches across three existing control families, one
task per family, one repeat, three policies and two execution profiles. Each role
has four cases. Six reference slots follow, with three eligible per profile only
when all nine corresponding searches completed. Search and reference capacities
are pinned separately before dispatch. Every authorized parent claim keeps its
whole reservation; ineligible references record a zero-charge blocked decision.
The campaign verifies the complete retained prefix before later work and never
redispatches a claimed child.

The report compares receipt semantics, histories, selections, gate decisions,
reference scores and verified usage. It keeps every scheduled row and family,
including failures and unavailable pairs. Profile/journal identities and process
telemetry are excluded from semantic parity. Phase coverage distinguishes
practice, restoration, confirmation, conditional audit and reference operations;
unknown observations and usage remain explicit.

## Validation

| Run | Passed | Skipped | Scope |
| --- | ---: | ---: | --- |
| macOS/ARM64 new integration | 163 | 0 | All six M16 test modules |
| macOS/ARM64 compatibility | 1,034 | 9 | All 32 pre-existing CPU test modules; expected Linux-only skips |
| Offline Linux/ARM64 | 204 | 0 | All M16 tests, measured runner and native resource enforcement |

The two macOS runs cover distinct tests: 1,197 passed and nine skipped in total.
The Linux run overlaps that coverage and should be reported separately. macOS
used Python 3.13.1; Linux used Python 3.11.16 in the cached pinned Python image,
with one CPU, a 1 GiB memory cap, no network, a read-only source mount and a
separate evidence directory. All production source hashes stayed unchanged.

Real integration fixtures cover pass/veto confirmation, conditional audit, three
completed source-policy searches, an exhaustive practice reference and guarded
read-only replay. Deterministic faults cover exact charge/input checks before
side effects, primary versus postcheck failure, lost outcomes, every publication
boundary, bounded staging, malformed/rehashed records, drift, source selection
bindings and complete-prefix verification before later campaign work. Linux
resource tests exercise actual address-space denial, CPU-limit termination,
wall deadlines and worker reaping. These fixtures are separate from the declared
paired campaign and are not replacement research attempts.

Full Python Ruff and mypy checks passed, including 1,033 source files for mypy.
Protocol generation is in sync. The public workflow documentation is updated
across all eight repository entry points; TypeScript runtime parity remains
explicitly deferred.

## Declared paired run

The single macOS/ARM64 attempt completed **18 searches and six references** with
zero failed, uncertain, blocked or unstarted rows. All **12 paired comparisons**
matched across their declared semantic components. The source snapshot stayed
unchanged during its 169.8-second execution. No replacement attempt was made.

All 18 searches retained the existing joint confirmation veto and withheld audit.
This is expected behavior for these control families, not an acceptance failure
or a reason to alter the gate. The separate pass/veto integration fixtures above
exercise the conditional audit path.

The search ledger charged its full capacity of 162 proposals, 90 materialization
operations, 1,872 forward calls, 15,294 input tokens and 1,872 output tokens.
Completed search receipts verified 1,512 forward calls, 9,266 input tokens and
1,512 output tokens; unused capacity was not refunded. References separately
charged and verified 30 checkpoint-verification operations, 312 forward calls,
1,964 input tokens and 312 output tokens, with no proposals or new materialization.
No completed row had unknown usage.

The measured profile retained 228 inference outcomes: 117 practice, 27 restoration,
45 confirmation and 39 reference batches. All received accepted validation checks.
There were no audit batches in this declared cohort. Native receipt semantics,
proposal/trial histories, selections, confirmations, restoration checks,
reference scores and charges matched their measured counterparts.

Guarded `report` and completed `run` replay reproduced the identical report with
dispatch, materialization and publication functions blocked. All 17 input files
and 2,897 output files remained byte-identical. The report digest is
`20e7a118e7abdc1689d5adf29a0308d602a63220dba915ac1a4412488079a4e4`.

Raw declared-run inputs, journal files, environment/source pins and replay proof
remain under `runs/milestone16-validation/` in this worktree, outside Git. macOS
test receipts are `/private/tmp/autocontext-m16-macos-new.xml` and
`/private/tmp/autocontext-m16-macos-compatibility.xml`. Linux receipts and source
hashes are under `/private/tmp/autocontext-m16-linux-evidence/`; the command and
log are beside that directory. The adjacent JSON retains the report, contract,
runtime, implementation and ledger identities, per-module counts and paired
comparison results.

## Compatibility and evidence preservation

All 74 pre-existing weight-evolution source files remain byte-identical. The
M11–M14 implementation digests are unchanged. Adding M16 modules changes M15's
package-wide measured profile, so new evidence has a new identity. Original M15
acceptance was replayed from its original `cacc558d6ee4451a9fb6070aae8d8b6bcbe6c1d3`
snapshot with both inference functions blocked: its nine retained files and
successful report were unchanged. No earlier pilot was replaced or relabeled.

## Remaining gaps

- M16 Linux/amd64 CI remains pending. Local Linux/ARM64 tests cannot close that
  architecture-specific gap.
- This bounded integration comparison is separate from the original M13 failure
  and the M14 follow-up. It cannot recover missing historical messages, RSS or
  process observations. No new 72-search pilot is part of this milestone.
- Existing control-family joint vetoes remain valid outcomes. Separate pass/veto
  fixtures exercise audit routing; they do not redesign the control families or
  establish scientific transfer, benchmark quality or adaptive-search advantage.
- Live proposals, larger models, arbitrary worker programs, TypeScript parity and
  promotion remain deferred.
- Qwen GPU acceptance, full base-versus-patch inference and benchmark reproduction
  remain unexecuted. This milestone runs only the tiny fixed CPU model.
