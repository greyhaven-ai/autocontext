# Milestone 13 validation

This milestone implements separately pinned CPU transfer controls in isolated
branch `codex/cpu-transfer-controls`, based on merged main
`23ee01e1cb6ccd3a45431823e291ebbccb4661d6`. GPU execution remains deferred.

## Contract and construction

M12 found that adaptive C/W search reached its declared practice maxima, while
all nine positive W practice gains failed confirmation. M13 adds constructed
calibration tasks to distinguish a reusable weight correction, a correct
baseline and a practice-specific shortcut under the existing search workflow.

The new contract pins all three families, neutral task/option identities,
generation/inference/search-order seeds, structural cohort identities, native
checkpoint edits, expected control behavior, scorer, source closure and complete
capacity. A separate adapter validates these tasks; they are never relabeled as
M11 tasks or passed through a relaxed version of its provenance validator.

Every task uses the same stable two-feature teacher. Positive and negative
evidence tokens are mutually exclusive; vocabulary coordinates are permuted by
the generation seed. Balanced labels are part of the population definition.
Inert token counts provide structurally distinct input rays across practice,
confirmation and audit. The generator rejects only structural collisions, never
an inference score or policy outcome.

| Constructed family | Baseline on every split | Designated W practice | Designated W confirmation/audit |
| --- | ---: | ---: | ---: |
| Transfer repair | 50% | 100% | 100% |
| Correct baseline | 100% | 50% | 50% |
| Practice shortcut | 50% | 100% | 0% |

For repair, the base flips the negative-evidence coefficient and the designated
edits restore its sign. The null control starts at the teacher. The shortcut
control starts with constant logits and edits a spurious feature whose label
association reverses outside practice; the underlying teacher remains unchanged.
Two additional W options edit an unused feature and tie the baseline. Candidate
lane placement, lane names and option IDs are seeded independently; repair
options are not consistently first or named as answers.

These percentages are analytical control expectations, not unseen-population
claims. Generator unit tests independently materialize and evaluate the expected
behaviors across all constructed roles. Campaigns evaluate only their frozen
selected candidates at confirmation and conditionally at audit. Their exhaustive
references remain practice-only; unit checks do not supply policy feedback.

The fixture pins six tasks, one repeat and three policies (18 searches). The
pilot pins twelve tasks, two repeats and three policies (72 searches). Both use
eight cases per role, four options per C/W/D mechanism and three trials per
mechanism. Limits remain bounded at 512 MiB, five seconds per worker batch and
14 input tokens per request. The same adaptive/fixed/random policies, earliest
practice-winner rule and 0.1 joint confirmation threshold apply to every family.

## Execution and reporting

Search uses the existing native checkpoint registry, CPU worker, exact-label
scorer, durable operation claims and immutable selection/confirmation seals.
Teacher, family and expected-control metadata remain in coordinator provenance;
policy inputs contain only ordinary options and practice feedback.

One separately charged reference per task/repeat probes baseline plus all twelve
declared options. Source searches must all complete before its plan can be
created. References reuse existing checkpoint bytes and charge five verification
operations to the existing `materializations` meter without creating new edits.
Failed or uncertain operations retain reservations and cannot be retried.

Reports preserve every search and reference, including missing results. Family
summaries use task means over nested repeats, expose opportunity and selected
practice gaps, and compare observed confirmation with analytical expectations.
A calibration mismatch is reported rather than filtered out. The projected
diagnostic evidence binds the original M12 report digest while omitting its
global policy summary; quality summaries remain within declared families.
No pooled winner, new confidence interval or inferential test is introduced.

The positive control targets the W component only. A W confirmation pass cannot
unlock audit while C or D fails the unchanged joint gate. Analytical audit
expectations must not be presented as observed audit results after a veto.

## Measured results

The fixture completed all 18 searches and six practice references. Each policy
observed the intended W behavior in both instances of every family: repair
improved practice and confirmation accuracy by 0.5; the correct baseline was
retained; and shortcut practice gains of 0.5 became confirmation losses of 0.5.
All 36 calibration checks matched, with zero mismatches or unavailable checks.
Every selected practice gap was zero within the declared option space.

All 18 fixture searches retained their joint vetoes because C/D did not improve;
no audit ran. The six references used 78 batches, 624 forward passes/output
tokens and 4,474 input tokens. These charges remain separate from the source
searches' 3,024 forward passes/output tokens, 20,796 input tokens, 162 proposals
and 90 materialization charges. Reference verification charged 30 existing
checkpoint checks to the materializations meter without creating new edits.

The pilot attempted all 72 scheduled searches: 71 completed with joint vetoes
and one failed with `CpuRunnerError`, surfaced as `CpuSearchBlockedError`.
The failed row is `task-01.repeat-01.fixed-order-v1`, during the eight-case
`confirmation-CW` batch. Its complete capacity reservation of 208 forward
passes/output tokens, 1,643 input tokens, five materializations and nine
proposals remains reserved against the campaign budget; actual usage is unknown
at the campaign level.
The existing journal retains the exception class, not the worker's detailed
message, so the underlying cause is unresolved. No search was retried or
replaced, and no task, seed or limit was changed in response to the outcome.

The pilot reference plan is correctly blocked by its incomplete source
campaign. None of its 24 intended references ran. The complete calibration
conclusions above therefore apply to the fixture; the pilot is retained as
partial source evidence, including its failure and missing reference coverage.

Completed-run resume and read-only reports matched retained reports without
inference or materialization. The fixture preserved 26 bundle files, 1,875
source files and 251 plan/reference files. The partial pilot preserved 50 bundle
files and 7,486 source files; blocked plan creation wrote no plan files.

Automated validation includes 117 milestone-specific tests and a broader
regression run with 2,184 passing and 11 skipped tests on macOS/ARM64 with
CPython 3.13.1. Skips cover platform-specific, pinned-scorer and explicitly gated
Docker/GPU checks. Ruff passed over source, tests and the example; mypy passed over 1,016
source/example files. Both campaign and diagnostic CLI validation commands
completed without execution. Counts, costs, identities and replay receipts are
recorded in [MILESTONE13_RESULTS.json](MILESTONE13_RESULTS.json).

Offline Linux/ARM64 acceptance passed all 21 CPU test modules: 697 passed, one
expected Darwin-only skip, zero failures/errors in 836.46 seconds. This includes
all 117 M13 tests and 17 resource-enforcement tests. The read-only container used
CPython 3.11.16 on Linux 7.0.14/glibc 2.41, no network, one CPU and a 768 MiB
memory limit. All 140 enumerated implementation, test, lock, bundle and CLI
hashes remained unchanged. The cached image identity is
`python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e`.

## Remaining gaps

- This change still requires Linux/amd64 GitHub CI. Local Linux acceptance uses
  ARM64; M12's successful amd64 CI does not validate M13 additions.
- The pilot has one unresolved worker failure and no exhaustive references.
  Diagnosing the worker failure needs richer evidence than the existing
  exception-class journal. A future pilot must be separately declared and keep
  this failed campaign visible; it cannot silently replace or retry its row.
- These are deliberately constructed calibration controls on a tiny linear CPU
  model. They establish expected workflow behavior, not language generalization,
  a natural-task benchmark gain or an adaptive advantage. Policies and the joint
  gate were kept fixed; D-policy changes remain a separate future experiment.
- macOS uses sampled RSS with possible overshoot; Linux enforces address-space
  and CPU limits plus parent wall deadlines. This worker runs fixed trusted code,
  not arbitrary programs. Local hashes are not externally signed provenance or
  a distributed transaction boundary against noncooperating writers.
- GPU/Qwen inference acceptance and the published base-versus-patch reproduction
  remain unexecuted. Live proposals, larger CPU models, arbitrary programs,
  TypeScript runtime parity and promotion remain deferred. No GPU or paid remote
  compute was used.

## Compatibility

M11/M12 contracts, source pins and evidence are unchanged. A read-only M12 pilot
replay from the new worktree verified all 72 source searches and 24 references,
matched the retained report exactly and preserved all 8,514 source/bundle/plan/
diagnostic files. Guards rejected any attempted inference, materialization,
filesystem mutation or writable file-open flags; none occurred.

Python remains the first runtime because this experiment reuses its CPU search
and diagnostic evidence. Public documentation is updated across the required
README, examples, integration, contributor and changelog surfaces.
