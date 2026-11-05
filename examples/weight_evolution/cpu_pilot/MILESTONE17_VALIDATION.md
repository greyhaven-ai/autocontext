# Milestone 17 validation

Implemented in the isolated `codex/cpu-measured-pilot` worktree from merged M16,
`2af945996214e5212abb8c000791553392b488a1`. GPU execution remains deferred.
The local validation below records the pre-PR implementation snapshot. The
CI follow-up records its subsequent Linux/amd64 run and deadline correction.

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
installs locked dependencies and permits one declared attempt with a 165-minute
acceptance-step deadline inside a 180-minute job limit. The shorter step limit
reserves time for evidence upload. The acceptance driver requires a fresh output directory, captures
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

At the pre-PR snapshot, the full **72-search / 24-reference pilot had not
executed**. Those local results do not claim pilot completion, calibration,
comparative policy results or full-cohort timing. Subsequent CI evidence follows.

## CI follow-up

[Standard CI run 42074414451](https://github.com/greyhaven-ai/autocontext/actions/runs/42074414451)
passed all 33 jobs at initial head `41fc04103b72ae6797edb5363deb5a36efb2bce9`.
All 124 M17 tests passed; the 43 CPU modules passed 1,329 tests with one expected
Darwin-only skip on Ubuntu 26.04.1/X64, CPython 3.11.18. All five downloaded
artifact ZIP digests matched GitHub's published values.

[Pilot run 42074414911](https://github.com/greyhaven-ai/autocontext/actions/runs/42074414911),
attempt 1 on that same head, was cancelled at its original 90-minute job limit.
The preserved artifact contains 86 completed parent claims, outcomes and
results: all 72 searches and 14 of 24 references, with no orphan parent claim
or recorded child failure. No full report, guarded replay or acceptance receipt
was published, so full calibration and acceptance remain unverified.
The artifact ZIP SHA-256 is
`fd4cad83044b2dc63230db6ced5efb5fe034cd74678d367af5e1f76b00f6923b`.

Filesystem publication timestamps show reference intervals increasing from
96 to 158 seconds, consistent with growing prefix-verification work. These timestamps
are advisory runtime evidence, not measured worker durations or a completion
guarantee. The workflow now allows 165 minutes for acceptance and 180 minutes
for the job, and logs advisory progress counts. Scientific contracts, budgets,
worker limits, all production sources and acceptance criteria are unchanged.
The original incomplete attempt remains retained; a fresh run of the corrected
workflow must pass before native Linux/amd64 full-pilot acceptance is claimed.

The correction passed all **131 M17 tests** on macOS/ARM64 (zero failures or
skips), including seven new progress-observer cases. Targeted Ruff and mypy,
JSON/workflow validation and whitespace checks passed. Independent review
confirmed all 85 production files remain byte-identical to the original
attempt's source inventory. The new progress observer only scans bounded
directory entries and writes advisory stderr logs; guarded replay still blocks
evidence writes and worker dispatch.

[Corrected-head standard CI run 42091271478](https://github.com/greyhaven-ai/autocontext/actions/runs/42091271478)
then hit the separate 30-minute Python shard deadline on head
`4ab1259ba85d961efa7f75c8b8642bdb41b3f264`: shard 2 was cancelled and the
aggregate test gate failed, while 31 jobs passed. No assertion failure was
printed, and shard 2 did not publish JUnit or coverage. Prior successful JUnit
durations sum to 25m12s for its current module roster; the expensive 28-test
pilot module alone previously took 12m45s. Current progress was about 20%
slower before that block, consistent with exceeding the job allowance.
Python shards now allow 40 minutes for tests inside a 45-minute job limit.
No test, assertion, coverage requirement or shard selection changed. This
workflow-only correction leaves all 93 files pinned by pilot acceptance
unchanged; new-head CI and full-pilot acceptance still must finish.

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
