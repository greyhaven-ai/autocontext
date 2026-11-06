# Milestone 18 validation

Implemented on `codex/cpu-joint-controls`, based on merged M17
`dd0d54ff52b788adcc912f88588759ab7348036d`. Validation used the pre-commit M18
source snapshot recorded in [machine-readable results](MILESTONE18_RESULTS.json).
Its 100 source pins were rechecked before publication. All 85 earlier production
modules remain byte-identical; seven new modules provide the task/oracle and
measured campaign contracts. GPU execution stayed deferred.

## Implementation

- Three deterministic task families: joint positive, correct baseline and
  practice-only repair. Each has eight cases per role and distinct normalized
  input rays. Seeds, opaque options, policies, thresholds and budgets are fixed.
- A separately implemented arithmetic oracle freezes full per-case expectations
  in a pinned 728,015-byte artifact before dispatch. Regeneration rejects
  consistently rehashed task/oracle drift. Expected metadata never enters
  search inputs, and selection uses practice observations only.
- Eighteen equal-budget measured searches precede six conditional exhaustive
  practice references. Claims retain reservations, failed/uncertain rows remain
  visible, and claimed work cannot dispatch twice. The existing C/W/D gate and
  measured execution adapters remain unchanged.
- CPU-only Linux/amd64 acceptance checks all 342 full-score/selection/gate
  calibrations and actual audit routing, retains partial attempts, and guards
  report plus terminal replay against dispatch and mutation.

## Checks

| Check | Result |
| --- | --- |
| Ruff, all Python source/tests and new examples | Passed |
| Mypy, source and new examples | Passed, 1,049 files |
| macOS CPU suite | 1,533 passed, 9 expected Linux-only skips across 50 modules |
| New M18 tests within that suite | 205 passed, no skips |
| Supplemental Linux/ARM64 fixtures and runner/resource tests | 246 passed, no skips |
| Earlier production source comparison | All 85 unchanged |
| Whitespace check | Passed |

The macOS run used Python 3.13.1. The supplemental Linux
run used Python 3.11.16 in the cached pinned image, with networking disabled,
read-only repository source, one CPU and a 1 GiB memory cap. It is Linux/ARM64
evidence, **not native Linux/amd64 acceptance**.

## One measured CPU attempt

All 18 searches and six references completed. **342/342 calibration checks
matched**, including ordered per-case predictions and correctness. Six positive
gates executed 30 audit batches; twelve vetoes withheld audit. No failed,
uncertain, blocked or unstarted rows remained.

Independent read-only verification checked 24 parent chains, 3,387 record
envelopes, 768 completed operations and all 486 observed/reaped inference batches.
The batch phases were 234 practice, 54 restoration, 90 confirmation, 30 audit and
78 reference. All 100 source pins matched the current worktree. Guarded report
and terminal replay preserved 18 bundle files and 3,772 campaign files.

The declared attempt and replay took 584.24s.
Observed worker durations sum to 133.21s;
that sum is not the total campaign cost. Full claimed search capacity remained
charged (3,744 forward calls); verified completed search usage was 3,264 calls.
References separately charged and used 624 calls. Every meter is retained in
the results artifact.

The positive family matched A=0.50, C=0.75, W=0.75 and D=1.00 on confirmation.
The negative families had an already-correct confirmation baseline, so their
selected changes provided no additional gain. All 24 policy confirmation
contrasts and 54 practice-reference gaps were zero. An adaptive win was not an
acceptance requirement and is not established by these results.

Report digest: `8a59a656cf16a0a2427bcef53485533d251aa2fddb73eb143fd828e7602e5639`.
Oracle SHA-256: `c9346d3f0b1206bc2383fbc0a5de6ee9a91a089b3ecef231587b7ce6647f4f96`.

Retained local evidence:

- `/private/tmp/autocontext-m18-linux-evidence/`: exact source inventory, JUnit,
  raw attempt, bundle/oracle, child and parent journals, report, guarded replay,
  validation receipt and independent audit.
- `/private/tmp/autocontext-m18-macos-tests.xml` and
  `/private/tmp/autocontext-m18-macos-tests.log`: complete CPU regression results.

Raw journals are retained outside Git. No attempt was replaced or retried.

## Remaining gaps

Native Linux/amd64 acceptance and PR CI were pending at publication; the dedicated
workflow was implemented but had not run during local validation. Process
peak RSS is unavailable in this run's measured process observations; enforced
address-space limits and worker telemetry are separate facts.

One constructed instance per family and two nested repeats do not establish
generalization or adaptive advantage. Decoding results are conditional on the
two frozen seeds. GPU inference, Qwen base-versus-patch reproduction, natural-task
transfer, TypeScript runtime parity and promotion remain deferred.
