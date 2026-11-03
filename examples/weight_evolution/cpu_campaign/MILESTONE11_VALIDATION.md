# Milestone 11 validation

This milestone implements pinned generated CPU campaigns and equal-capacity
adaptive, fixed-order, and seeded-random search. GPU execution remains deferred.
The isolated branch is `codex/cpu-search-campaign`, based on merged main
`d97816c3ba14209f75d1972ce7958446fdc4e354`.

## Contract and execution

- The checked-in fixture declares 3 generated task instances, 1 repeat and all
  3 policies: 9 searches. The separately generated pilot declares 8 tasks,
  3 repeats and 3 policies: 72 searches.
- Each task has 4 examples per practice/confirmation/audit split, an independently
  generated tiny base checkpoint and latent label rule, and 4 options per C/W/D
  mechanism. Three candidates per mechanism are tried; all policies select the
  earliest practice winner, including the unchanged baseline.
- SHA-256 generation, ordering, and inference seeds have separate roles. Task
  bytes and structural split identities are regenerated and checked before use.
  Permutations and proportional token repetitions cannot cross split identities.
- The full roster, scorer, caps, source identities, one-look confirmation and
  eight task-level contrasts are pinned before execution. CW combines selected
  C and W without another search. Failed/uncertain runs retain their full
  reservations; their actual unknown charges are not reported as zero.
- Every completed child is replay-verified, including its checkpoint bytes and
  receipts. An interrupted child's valid completed prefix is checked before its
  unresolved operation is marked blocked. Corruption raises an integrity error
  before another child can dispatch. A completed child missing a final result
  cannot be relabeled as an ordinary failure.
- Read-only reporting opens existing locks without write/create flags and never
  dispatches inference, materializes candidates, repairs reports, or writes files.
  Inventory traversal stops at its entry cap before sorting or hashing.

## Validation results

| Check | Result |
| --- | --- |
| macOS/ARM64 broader regression, CPython 3.13.1 | 1,957 passed, 11 skipped; 311.98 s |
| Linux/ARM64 CPU suite, CPython 3.11.16 | 470 passed, 1 Darwin-only skip; 256.34 s |
| Ruff: source, tests, M10/M11 Python examples | Passed |
| Mypy: source and M11 examples | 1,006 files passed |
| Local Markdown links | 448 links across 168 documents passed |
| Whitespace/diff check | Passed |

The broader regression covers all `test_weight_*.py` modules plus dictionary-type,
Docker ownership/worker, protected authority, kernel generation/budget, and
training confirmation tests. Its skips require Linux-specific enforcement,
explicit external scorer assets, a real Docker/MIG gate, or native Windows.
The Linux run exercises all CPU modules, including real address-space denial,
CPU-limit termination and wall-deadline cleanup. It uses the cached digest-pinned
Python container with no network, a read-only checkout, 768 MiB memory and one CPU.
Source/test/bundle hashes were identical before and after the suite.

Coverage includes deterministic task regeneration, independent native tensor-edit
verification, structural split aliases, policy order and practice-only inputs,
matched capacities, earlier-best restoration, confirmation/veto behavior,
nested-repeat statistics, full failure denominators, interrupted publication,
read-only replay, evidence tampering, bounded traversal and source drift.

Both CPU campaigns completed, and repeated `run` plus read-only `report` preserved
all retained file bytes. Machine-readable summaries are in
[MILESTONE11_RESULTS.json](MILESTONE11_RESULTS.json); full journals remain under
the ignored `runs/milestone11-reviewed-validation/` directory.

| Measurement | Fixture | Pilot |
| --- | ---: | ---: |
| Generated task instances | 3 | 8 |
| Repeats per task | 1 | 3 |
| Scheduled/completed searches | 9 / 9 | 72 / 72 |
| Execution failures | 0 | 0 |
| Confirmation vetoes | 9 | 72 |
| Conditional audits available | 0 | 0 |
| Proposal charges | 81 | 648 |
| Materialization/verification charges | 45 | 360 |
| Reserved forward-pass capacity | 936 | 7,488 |
| Actual forward passes/output tokens | 756 | 6,048 |
| Actual input tokens | 5,007 | 41,499 |
| Byte-preserved retained files | 939 | 7,491 |

No run passed the joint C/W/D confirmation gate, so every audit was correctly
withheld. These are completed, vetoed searches, not missing data or execution
failures. The fixture showed zero observed differences between policies.

Pilot confirmation comparisons below use the eight task means, each averaging
three paired repeats. Values are percentage-point differences in accuracy;
intervals use the predeclared paired-t/Bonferroni-eight rule.

| Adaptive minus control | Condition | Mean | Interval |
| --- | --- | ---: | --- |
| Fixed order | C | +9.38 | [-26.77, +45.52] |
| Fixed order | W | 0.00 | [0.00, 0.00] |
| Fixed order | CW | +9.38 | [-26.77, +45.52] |
| Fixed order | D | +2.08 | [-3.17, +7.34] |
| Seeded random | C | 0.00 | [0.00, 0.00] |
| Seeded random | W | -1.04 | [-5.06, +2.97] |
| Seeded random | CW | -1.04 | [-5.06, +2.97] |
| Seeded random | D | -3.13 | [-20.23, +13.98] |

Every nonzero interval includes zero; this pilot does not establish an adaptive
advantage. Zero-width intervals reflect zero observed task-level variation in
these small samples, not certainty about an unseen population. No overall audit
quality aggregate is reported.

Retained identities:

- Fixture campaign: `a995683c63387e6aa64be7d3a57d316b21c38a1f306fb772e49139f0116733e4`.
- Pilot campaign: `ee57f595fa2ca10c02b8625f25a2beee6cf4eded8d4daf41d472f54d6eb3b36c`.
- Pilot semantic report: `1c4d0a3d4c589afbb05045f7cf4e08605d9778a858f54f982d23d0f61db0d165`.
- Shared local CPU runtime: `f4c588e1e9586095b280e2604d6b414ad7db181db6f602ff45426dda19089b00`.

The repinned M10 smoke preserved its earlier C/W/D winners, completed its audit,
and used 130 forward passes, 255 input tokens and 9 proposals.

## Remaining gaps

- New milestone 11 tests have not yet run in Linux/amd64 GitHub CI. Milestone 10
  previously passed amd64 acceptance; that is not a substitute for this change's
  CI results. Local Linux validation uses an offline ARM64 container.
- macOS uses sampled RSS monitoring, with the documented possible overshoot;
  Linux exercises address-space and CPU limits. The worker is fixed trusted code,
  not an arbitrary-code sandbox. Local file hashes provide integrity checks, not
  externally signed authorship or a distributed transaction boundary.
- These are small generated linear-classifier tasks. Structurally distinct inputs
  and separate seed streams do not establish semantic independence or language
  generalization. Repeats are nested within tasks; model-based t intervals are
  descriptive and have limited precision at eight independent task instances.
- Audit scores are conditional on confirmation and cannot support an overall
  quality mean across only survivors. A veto is retained as a completed run,
  not silently dropped or retried.
- Qwen checkpoint/GPU inference acceptance and the full published base-versus-patch
  reproduction remain unexecuted. Live model proposals, arbitrary edit programs,
  larger CPU models, TypeScript parity, and benchmark/promotion claims remain
  deferred. No GPU or paid remote compute was used.

## Compatibility

The M10 example is repinned for explicit policy provenance and the strengthened
interrupted-prefix validation. Its checkpoint, candidate choices, selection,
restoration and confirmation behavior are unchanged. Historical M10 evidence
retains its original source pins. M9 and the earlier Qwen/fixture contracts are
unchanged. The campaign reuses the CPU runner, tensor registry, journals, scorer,
and shared paired-statistics helper; kernel-specific speedup and promotion schemas
are not repurposed as CPU evidence.
