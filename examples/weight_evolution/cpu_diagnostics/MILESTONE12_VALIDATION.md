# Milestone 12 validation

This milestone adds a pinned diagnostic contract and an exhaustive practice-only
reference for completed CPU search campaigns. It is implemented in the isolated
`codex/cpu-search-diagnostics` branch, based on merged main
`95b9713db02838546dbeae9d6e723f4cb4f4526c`. GPU execution remains deferred.

## Contract and execution

- Plan creation independently replays every retained source search and requires
  the complete original roster. Completed vetoes are eligible; failed, pending,
  and unstarted source searches are not. Full campaign evidence, selections,
  summaries, option order, practice cohort, runtime, seeds, scorer and diagnostic
  implementation are frozen before reference dispatch.
- Each task/repeat gets one shared reference for all three policies. The baseline
  runs once, followed by every declared single-surface C/W/D option. The M11
  family has four options per mechanism, giving 13 complete-cohort batches per
  reference. No confirmation/audit inputs enter those batches; labels remain
  with the coordinator.
- Baseline and W artifacts are verified in the original adaptive search's
  read-only checkpoint store. The existing `materializations` meter charges
  checkpoint verification, five per reference, without creating edits. Reference
  capacity and charges remain separate from the original search budget.
- Durable claims precede every verification and inference operation. A valid
  interrupted prefix resumes only unclaimed work. Failed/uncertain operations
  cannot be retried and retain their full reference reservations. Unknown actual
  costs and missing opportunity metrics remain unknown, rather than zero.
- Completed references are replay-verified before new dispatch. Missing completed
  evidence is an integrity error. Read-only reporting opens existing locks
  without creation/write flags, verifies completed references, and leaves an
  unsealed pending child's partial journal untouched. Resume verifies that
  child's prefix before continuing or recording its uncertain failure.
- Bounded inventories reject excess entries before collecting them. Source,
  plan and output paths must be disjoint after normalization; symlinks and `..`
  aliases cannot bypass that boundary.

## Retained fixture and pilot

The original M11 searches were reused without executing them again or changing
any M11 implementation or asset pins. Both reference schedules completed; repeated `run`
and read-only `report` dispatched no new inference and preserved every retained
file. Source snapshots include checkpoint stores, selections, receipts, reports,
locks and journals.

| Measurement | Fixture | Pilot |
| --- | ---: | ---: |
| Task instances | 3 | 8 |
| Repeats per task | 1 | 3 |
| Original completed policy searches | 9 | 72 |
| Scheduled/completed references | 3 / 3 | 24 / 24 |
| Reference failures | 0 | 0 |
| CPU batches | 39 | 312 |
| Forward passes/output tokens | 156 | 1,248 |
| Input tokens | 1,047 | 8,532 |
| Checkpoint-verification charges | 15 | 120 |
| Proposal calls/new materializations | 0 / 0 | 0 / 0 |
| Byte-preserved source bundle files | 14 | 34 |
| Byte-preserved source evidence files | 939 | 7,491 |
| Byte-preserved diagnostic plan/evidence files | 128 | 989 |

All 72 pilot searches retain their original joint confirmation veto; the
diagnostic neither reselects candidates nor opens a new held-out evaluation.
Counts below describe 24 task/repeat references, nested within eight task
instances. They are not 24 independent task samples.

| Pilot reference property | C | W | D |
| --- | ---: | ---: | ---: |
| References with a declared practice improvement | 9 | 9 | 11 |
| References with no declared practice improvement | 15 | 15 | 13 |
| References where all options and baseline have identical output vectors | 12 | 9 | 8 |
| Mean distinct output vectors, including baseline | 1.875 | 1.750 | 2.125 |

The next table counts searches whose selected practice score fell below the best
declared practice option. A search can improve over baseline and still miss a
larger available improvement. Means are accuracy percentage points across all
24 runs of each policy, including zero gaps; no new intervals or inferential tests are added.

| Policy | C missed / mean gap | W missed / mean gap | D missed / mean gap |
| --- | ---: | ---: | ---: |
| Adaptive | 0 / 0.00 | 0 / 0.00 | 4 / 6.25 |
| Fixed order | 3 / 3.125 | 0 / 0.00 | 6 / 9.375 |
| Seeded random | 0 / 0.00 | 0 / 0.00 | 3 / 4.167 |

Adaptive search reached the declared practice maximum for C and W in every
pilot reference. Yet all nine positive W practice gains failed the original W
confirmation component; six of nine positive C gains and five of seven positive
D gains also failed their respective components. The three-task fixture has
zero selected gaps for every policy and mechanism; its single positive C
practice improvement fails confirmation for every policy.

These results point to task/candidate design and practice-to-confirmation
transfer as the next experiments to investigate. Increasing the existing W
search budget alone would not improve its measured practice maximum. D retains
measurable coverage gaps worth testing under a newly pinned policy
comparison. A future task family should include controlled positive and negative
cases and keep the original evidence frozen. Nothing here justifies relaxing
the joint gate or asserting that an unselected practice winner generalizes.

Machine-readable summaries and identities are retained in
[MILESTONE12_RESULTS.json](MILESTONE12_RESULTS.json). Full local plans, journals,
reports and source snapshots are under ignored `runs/milestone12-validation/`.
The plans bind actual retained source receipts and runtime; they are evidence
for this run, not portable substitutes for a freshly completed source campaign.

## Validation results

| Check | Result |
| --- | --- |
| Focused M12 contracts, plan, report and integration tests | 110 passed; 89.06 s |
| macOS/ARM64 broader regression, CPython 3.13.1 | 2,067 passed, 11 skipped; 394.48 s |
| Linux/ARM64 CPU suite, CPython 3.11.16 | 580 passed, 1 Darwin-only skip; 366.08 s |
| Ruff: source, tests and M12 CLI | Passed |
| Mypy: source and M12 CLI | 1,009 source files plus CLI passed |
| CLI help and retained fixture `validate` | Passed without model execution |
| Local Markdown links | 458 links across 170 documents passed |
| Whitespace/diff check | Passed |

The broader regression covers every `test_weight_*.py` module plus dictionary
consistency, Docker ownership/worker, protected kernel authority, kernel campaign
and budget, and training confirmation tests. Its skips require Linux enforcement,
explicit external scorer assets, a real Docker/MIG gate, or native Windows.

The Linux run covers all 17 CPU modules, including real address-space denial,
CPU-limit termination and wall-deadline cleanup. It uses the cached digest-pinned
Python container, offline pinned wheels, no network, a read-only checkout,
768 MiB memory, one CPU, and bounded pytest/container deadlines. All 102 captured
implementation, test, bundle and example-file hashes remained unchanged. The
single Linux skip tests the Darwin RSS monitor.

Offline diagnostic fixtures cover no available improvement, missed better
options, output-vector ties, baseline retention, practice gains that fail
confirmation, missing/failed reference denominators, score/provenance mismatch,
complete request coverage, distinct reference charges, source immutability,
interrupted operations and parent publication, valid-prefix resume, no-work
completed replay, missing completed evidence, bounds, symlinks and path aliases.

## Remaining gaps

- New milestone 12 changes still require Linux/amd64 GitHub CI. Local Linux
  validation uses ARM64; M11's successful amd64 CI does not cover these additions.
- The CPU runner reuses Linux address-space/CPU limits and hard wall deadlines.
  macOS uses sampled RSS with possible overshoot. It executes fixed trusted code,
  not arbitrary edit programs. Local hashes provide integrity, not externally
  signed provenance or a distributed transaction boundary against concurrent
  noncooperating writers.
- References exhaust only the declared finite single-surface practice options
  with fixed seeds. CW remains the original selected composition. These small
  generated linear-classifier tasks do not establish language generalization,
  an exhaustive held-out optimum, or an adaptive benchmark advantage.
- GPU/Qwen inference acceptance and the published base-versus-patch reproduction
  remain unexecuted. Live proposal models, larger CPU models, arbitrary programs,
  TypeScript parity and promotion remain deferred. No GPU or paid remote compute
  was used.

## Compatibility

The implementation reuses the existing CPU runner, native checkpoint verifier,
read-only registry, immutable-record publication, campaign replay and exact-label
scorer. New contracts, journals and reporting modules are separate from M11;
existing source closures, fixture bundles, original search budgets, selections,
confirmation gates and earlier Qwen contracts are unchanged. Python is the first
runtime because its retained campaign and CPU-worker evidence are authoritative
for this experiment; TypeScript parity remains deferred.
