# Milestone 20 validation

Implemented on `codex/cpu-search-robustness`, based on merged M19
`964f165250304e124385caec364f19138fd797c3`. Validation used the frozen uncommitted source snapshot
recorded in [machine-readable results](MILESTONE20_RESULTS.json). All 114 attempt
source pins, all 65 CPU test-snapshot pins and both validation-driver pins matched
after execution. All 99 earlier production modules remain byte-identical.
Seven new modules add the robustness contract, generator, oracle and measured
campaign. GPU execution stayed deferred.

## Implementation

- Six unchanged M19 base tasks retain option identities, checkpoint bytes,
  candidate semantics, role cases and neutral context/decoding anchors. Only W
  declaration order and trial budget vary. Generation happens before reordering,
  preserving M19's candidate margins and direction/step semantics.
- The measured panel has twelve three-round variants: six base tasks under
  canonical and reverse-lanes order. Three canonical six-round variants cover
  the first declared instance per family. One fixed seed and three unchanged
  policies produce 45 searches, all published before fifteen practice-only
  exhaustive references. References cannot select or reselect a candidate.
- The independent oracle also enumerates 90 legal W orders, budgets 1/3/6 and
  adaptive0/fixed0/random0/random1, producing 6,480 offline trajectories. These
  are construction expectations, separate from measured evidence. Option IDs
  remain fixed; no arbitrary renaming-invariance claim is made.
- Reports retain exact histories, ordered outputs, selections, reference gaps,
  all five cost meters and variable-length practice curves. Orders and budgets
  remain nested within six base tasks; neither variants nor seeds become
  independent task replications. No pooled adaptive-win threshold is required.

## Checks

| Check | Result |
| --- | --- |
| Ruff, Python source/tests and new examples | Passed |
| Mypy, Python source | Passed, 1,058 files |
| macOS CPU suite | 2,117 passed, 9 expected Linux-only skips across 64 modules |
| New M20 tests within that suite | 307 passed, no skips |
| Supplemental Linux/ARM64 | 348 passed, zero skips |
| Frozen attempt source and CPU test-snapshot pins | 114 and 65 verified |
| Existing production source comparison | All 99 unchanged |

macOS used Python 3.13.1. Supplemental Linux used Python
3.11.16 in the cached pinned image, with networking
disabled, read-only source, one CPU and a 1 GiB container memory cap. The Linux
checks include measured-runner cleanup and resource enforcement fixtures.
This is **Linux/ARM64 evidence, not native Linux/amd64 acceptance**.

After the frozen campaign and independent audit, final formatting removed two
extra EOF blank lines from `test_weight_cpu_robustness_campaign_report.py`.
Its AST, including source locations, remained identical; all 49 focused report
tests passed again. The results retain before/after hashes and the archived
historical test snapshot. All 114 measured source pins and the other 64 CPU
test-snapshot pins still match. Tracked and untracked whitespace checks and the
CPU-only workflow syntax/deadline/artifact checks passed.

## One measured CPU attempt

All **45 searches and fifteen references completed**, with **915/915 exact oracle
calibrations** and **90/90 measured robustness checks**. The 135 practice curves
contain 108 four-point and 27 seven-point histories. No failed, uncertain,
blocked or unstarted rows remained. No attempt was retried or replaced.

| Measured invariant | Matches |
| --- | --- |
| `reference-score-tables` | 6/6 |
| `seeded-random-declaration-order` | 6/6 |
| `full-budget-zero-practice-regret` | 9/9 |
| `short-full-budget-history-prefix` | 9/9 |
| `neutral-weight-null` | 15/15 |
| `neutral-context-decoding-anchors` | 45/45 |

Each check identity, expected value, observed value and source run binding is
retained in the machine-readable results. Score tables were identical under
declaration-order changes; seeded-random histories stayed fixed with IDs fixed.
Every measured six-round search reached zero practice gap, and its three-round
history was an exact prefix. Neutral W/C/D outcomes stayed null. Adaptive and
fixed-order outcomes were permitted to depend on declaration order.

The six order comparisons below give correct counts out of eight, in
**adaptive / fixed / seeded-random** order. Each row is one base task, not two
independent task observations. Complete expected/observed scores, regrets and
contrasts are retained in the results artifact.

| Base task | Family | Canonical counts | Reverse-lanes counts |
| --- | --- | --- | --- |
| task-01 | informative-improvement-v1 | 8/6/6 | 6/6/6 |
| task-02 | informative-improvement-v1 | 8/4/8 | 4/4/8 |
| task-03 | misleading-improvement-v1 | 6/8/8 | 8/8/8 |
| task-04 | misleading-improvement-v1 | 4/8/8 | 8/8/8 |
| task-05 | neutral-v1 | 4/4/4 | 4/4/4 |
| task-06 | neutral-v1 | 2/2/2 | 2/2/2 |

The canonical informative advantages and misleading-task deficits all disappeared
under reverse-lanes order: adaptive and fixed-order tied on every reversed
base task. This demonstrates declaration-order sensitivity within these six
constructed tasks; it does not establish a population-wide policy effect.

The nine budget comparisons are restricted to canonical order, seed zero and
the first declared instance per family. Regret is the practice reference's
best accuracy minus the selected practice accuracy.

| Base task | Policy | Three-round score | Six-round score | Three-round regret | Six-round regret |
| --- | --- | --- | --- | --- | --- |
| task-01 | adaptive-v1 | 8/8 | 8/8 | 0/8 | 0/8 |
| task-01 | fixed-order-v1 | 6/8 | 8/8 | 2/8 | 0/8 |
| task-01 | seeded-random-v1 | 6/8 | 8/8 | 2/8 | 0/8 |
| task-03 | adaptive-v1 | 6/8 | 8/8 | 2/8 | 0/8 |
| task-03 | fixed-order-v1 | 8/8 | 8/8 | 0/8 | 0/8 |
| task-03 | seeded-random-v1 | 8/8 | 8/8 | 0/8 | 0/8 |
| task-05 | adaptive-v1 | 4/8 | 4/8 | 0/8 | 0/8 |
| task-05 | fixed-order-v1 | 4/8 | 4/8 | 0/8 | 0/8 |
| task-05 | seeded-random-v1 | 4/8 | 4/8 | 0/8 | 0/8 |

The offline oracle independently verifies all **6,480 trajectories** over six
tasks, 90 legal orders, three budgets and four policy/seed cells. Score-table
invariance, seeded-random order invariance, full-budget zero regret, budget
prefixes and neutral nulls all hold within that finite construction. These
trajectories did not launch workers and do not increase the measured sample size.
Only two orders and seed zero were measured; budget one and random seed one
remain offline expectations.

The unchanged C/W/D confirmation gate vetoed all 45 searches because C and D
remain neutral. **No audit batch ran.** Hypothetical audit scores are oracle
expectations; they are not measured audit results. Positive joint routing and
non-neutral C/D calibration remain outside this campaign.

Independent read-only verification checked 60 parent chains,
9,726 record envelopes, 2,217 completed operations
and all **1,311 observed/reaped CPU inference batches**.
All 10,488 raw inference outputs,
735 full score objects, histories,
prefix curves, all policy regrets, six order comparisons, nine budget comparisons
and the complete offline domain matched the pinned oracle.

Guarded replay preserved 66 bundle files and 11,047 campaign files.
The declared attempt and replay took 2942.14 seconds;
observed worker durations sum to 436.35 seconds
and do not represent total campaign cost. Search reservations retained
10,008 forward calls; completed
search usage was 8,208 calls.
References separately charged and used
2,280 calls. Proposals,
materializations, inference calls, input tokens and output tokens remain separate.

Report digest: `f044517d814c09398acfb74184467d449b25adbd48581c9d926f6d5ac100c35b`.
Oracle SHA-256: `71fe8b5bbaae24525e1e9b220962be1223ca7a448e879947f562a4a75771924c` (2,379,507 bytes).

Raw journals and independent audit remain under `/private/tmp/autocontext-m20-linux-evidence/`.
macOS JUnit and logs remain at `/private/tmp/autocontext-m20-macos-tests.xml` and `/private/tmp/autocontext-m20-macos-tests.log`.
The results artifact pins those evidence files and every validated source file;
raw attempts remain outside Git.

## Remaining gaps

Native Linux/amd64 acceptance and PR CI are pending. The dedicated workflow
requires one fresh CPU-only attempt, retains failure evidence and checks exact
calibration, invariants and process cleanup. Supplemental ARM64 execution does
not close the native acceptance gap.

Six constructed teachers and nested order/budget conditions do not establish
natural-task transfer, an order-invariant adaptive advantage or population-wide
performance. The full-budget measured subset covers only first instances under
canonical order and seed zero. Neutral C/D behavior does not calibrate a general
context or decoding search, and joint audit remains withheld.

Resource enforcement covers the trusted fixed CPU model, not arbitrary untrusted
programs. Parent-observed process peak RSS was unavailable for every inference batch. Enforced address-space limits and worker telemetry remain
separate from parent-observed process peak RSS.

GPU inference, Qwen base-versus-patch benchmark reproduction, TypeScript runtime
parity and promotion remain deferred.
