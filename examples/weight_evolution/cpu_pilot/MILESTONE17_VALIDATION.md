# Milestone 17 validation

Implemented in the isolated `codex/cpu-measured-pilot` worktree from merged M16,
`2af945996214e5212abb8c000791553392b488a1`. GPU execution remains deferred.
This document distinguishes implementation validation from the full pilot,
which requires its own Linux/amd64 acceptance attempt.

## Contract and execution

The pilot fixes the original twelve M13 control tasks, both declared repeats,
three policies, eight cases per role, candidate options, scorer, worker limits
and joint confirmation gate. It schedules **72 measured searches**, followed by
**24 conditional practice-reference decisions**. All 72 searches must complete
and publish their results before any reference is eligible. Failed or uncertain
searches block all reference dispatch, retaining explicit zero-charge decisions.

The contract separately binds the pilot implementation, unchanged M16 adapters,
native runtime, measured profile, v2 failure codec, roster, schedule and exact
five-meter capacities. Validation regenerates original task assets and rejects
consistently rehashed changes to seeds, task order, cohorts or budgets.

| Meter | Search capacity | Separate reference capacity |
| --- | ---: | ---: |
| Proposals | 648 | 0 |
| Materializations / existing-checkpoint verifications | 360 | 120 |
| Forward calls | 14,976 | 2,496 |
| Input tokens | 129,288 | 17,688 |
| Output tokens | 14,976 | 2,496 |

Claims precede child dispatch. Returned outcomes precede terminal publication;
valid saved outcomes can recover missing publication, while claimed children
never execute again. Full reservations remain charged without refunds. Every
retained child and parent record is checked before later work. Reference plans
bind all 72 verified sources and pass only practice inputs to unchanged M16
reference adapters. A completed replay verifies evidence once under an existing
read lock and performs no directory creation, staging cleanup or publication.
Publication scratch uses the writer's 16 MiB per-file bound and a maximum of
16 staged files per directory. Read-only inspection preserves valid scratch;
resume can discard it but cannot promote staged-only bytes into outcomes.

## Reporting and acceptance

Reports retain all 96 scheduled slots, including missing work. Evidence integrity,
campaign completion and calibration acceptance are separate fields. A partial
pilot can have valid evidence without passing acceptance. All 144 declared
analytical W calibration checks must be available and match for acceptance;
expected joint C/W/D vetoes are valid outcomes and withhold audit.

Existing family-specific comparison and practice-reference calculations are
reused. Repeats are nested within tasks; incomplete family comparisons remain
unavailable. Reference gaps compare actual output vectors as well as accuracy.
Measured phase coverage, elapsed observations and observed RSS are reported
without inventing missing telemetry. Linux address-space enforcement is not
reported as an RSS measurement. Relative child-evidence paths locate retained
operation/failure records; observations do not establish a root cause.

The new CPU-only workflow checks out the exact PR head on Ubuntu 24.04/X64,
installs locked dependencies and permits one declared attempt with a 90-minute
job deadline. The acceptance driver requires a fresh output directory, captures
source/environment identities before execution and rejects incomplete or
miscalibrated pilots. Raw partial artifacts, hidden lock files and failure
receipts are uploaded even when the job fails. There is no automatic retry,
GPU setup or external model provider.

## Validation evidence

The adjacent [machine-readable results](MILESTONE17_RESULTS.json) records final
test counts, source identities, compatibility receipts and unexecuted stages.

| Validation | Passed | Skipped |
| --- | ---: | ---: |
| macOS/ARM64: all five new M17 modules, final source | 124 | 0 |
| macOS/ARM64: all 38 preexisting CPU modules | 1,197 | 9 expected Linux-only skips |
| Offline Linux/ARM64: M17 plus measured runner and resource enforcement | 165 | 0 |

The macOS rows cover distinct tests, totaling 1,321 passed and nine skipped.
The broad run was interrupted without failures to fix the publication-scratch
bound; remaining modules completed afterwards, and all M17 tests passed on the
final source. Counts exclude duplicate runs. Preexisting production files were
unchanged throughout. The Linux row overlaps that coverage and is reported
separately; its final 165-test run passed after the scratch fix.

macOS used CPython 3.13.1. Linux used CPython 3.11.16/aarch64 in a cached,
digest-pinned image with one CPU, a 1 GiB cap, no network and a read-only source
mount. All 101 captured Linux source/test/example/lock/workflow files remained
unchanged. Resource tests exercised actual address-space denial, CPU-limit
termination, wall deadlines and cleanup. This is ARM64 validation, not native
amd64 acceptance.

Ruff passed for all Python source, tests and new examples. Mypy passed across
1,037 source files; protocol generation, role-output schemas, dependency-boundary
scanner tests, the boundary ratchet, workflow structure/action pins and whitespace
checks passed. The final local weight-evolution inventory contains 85 unchanged
source hashes relative to its validated snapshot.

Offline fixtures exercise the complete roster and equal-budget accounting,
blocked references, uncertain usage, interruption/reconciliation, rehashed
drift, source selection bindings, family aggregation, calibration mismatches,
platform checks and write-protected replay. These constructed records are tests,
not full-pilot research results.

Real integration tests run the first task/repeat's three policy searches and one
13-batch exhaustive practice reference. The remaining source-plan bindings in
that test are explicitly synthetic. A separate guarded report over the three
actual search records correctly reports 69 unstarted searches and 24 unstarted
references rather than claiming a complete pilot.

The full **72-search / 24-reference pilot has not executed**. No pilot completion,
calibration result, comparative policy result or full-cohort timing is claimed.
Its first native Linux/amd64 attempt remains pending publication of this branch
and execution of the new workflow.

## Compatibility and remaining gaps

All 81 preexisting weight-evolution production files remain byte-identical to
merged M16. All 48 retained M14 task files match current deterministic generation.
Original M16 reporting was replayed from its original source snapshot with
dispatch, materialization, publication and writable evidence opens blocked:
17 input and 2,897 output files remained unchanged. Its retained report digest
is `20e7a118e7abdc1689d5adf29a0308d602a63220dba915ac1a4412488079a4e4`.

Adding M17 modules creates a new package-wide measured profile. Earlier evidence
keeps its original source/profile identity. M16's Linux/amd64 CI gap is already
closed; M17's new workflow and full cohort require their own CI evidence.

The original M13 failure's missing observations cannot be recovered. The current
control families remain W-component calibrations; positive joint-gate redesign,
scientific transfer and adaptive-search advantage are not established. Live
proposals, larger models, arbitrary worker programs, TypeScript runtime parity
and promotion remain deferred. GPU/Qwen acceptance and full base-versus-patch
benchmark reproduction remain unexecuted.
