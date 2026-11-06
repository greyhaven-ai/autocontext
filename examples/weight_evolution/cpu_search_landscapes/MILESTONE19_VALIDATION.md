# Milestone 19 validation

Implemented on `codex/cpu-search-landscapes`, based on merged M18
`0a8b38b8fb8ba0e7ce96adafc400549e3296c2d3`. Validation used the frozen uncommitted source snapshot
recorded in [machine-readable results](MILESTONE19_RESULTS.json). All 107 attempt
source pins and all supplemental test-snapshot pins matched after execution.
Pre-commit cleanup subsequently removed two extra EOF blank lines from one test
file; its 31 tests passed again. Historical test-snapshot hashes remain unchanged
in the retained evidence; all 107 measured-attempt source pins still match.
All 92 earlier production modules remain byte-identical; seven new modules add
separate landscape and measured campaign contracts. GPU execution stayed deferred.

## Implementation

- Six deterministic task instances cover informative improvement, misleading
  improvement and neutral behavior. Two instances per family differ in teacher,
  baseline corruption, margins and candidate effects. Two repeats remain nested
  within each task, with fixed inference/search-order seeds 0 and 1.
- Each unchanged policy receives three trials from six options per mechanism and
  eight cases per role. Thirty-six searches precede twelve exhaustive
  practice-only references. Full reservations, failures and uncertain outcomes
  remain visible; references cannot select or reselect search candidates.
- An independent arithmetic oracle pins complete scores, ordered per-case
  outputs, proposal histories, selections and reference gaps before workers run.
  Exact regeneration rejects consistently rehashed design drift. Control metadata
  stays outside policy inputs. Tests bind all 36 run expectations to a compact
  golden fixture and hand-derived weight-search trajectories.
- Reports retain all five cost meters and practice prefix curves without extra
  inference. Acceptance verifies exact expected wins, losses and nulls, complete
  observed/reaped process coverage and guarded report plus terminal replay.

## Checks

| Check | Result |
| --- | --- |
| Ruff, all Python source/tests and new examples | Passed |
| Mypy, source and new examples | Passed, 1,056 files |
| macOS CPU suite | 1,810 passed, nine expected Linux-only skips across 57 modules |
| New M19 tests within that suite | 277 passed, no skips |
| Supplemental Linux/ARM64 | 318 passed, zero skips |
| Existing production source comparison | All 92 unchanged |
| Workflow YAML and whitespace, including new files | Passed |

The final staged whitespace check caught the test-file EOF issue missed by the
earlier check. After cleanup, `git diff --cached --check` passed. The focused
rerun covered `test_weight_cpu_landscape_campaign_report.py` (31 passed, 6.29s);
no production source, campaign input, assertion or executable test code changed.

The macOS suite used Python 3.13.1. Supplemental Linux used the cached pinned
Python 3.11.16 image, networking disabled, read-only source, one CPU and a 1 GiB
container memory cap. The Linux checks include measured-runner cleanup and
resource enforcement fixtures. This is **Linux/ARM64 evidence, not native
Linux/amd64 acceptance**.

## One measured CPU attempt

All **36 searches and twelve references completed**, with **732/732 exact oracle
calibrations**, 108 four-point practice curves and twelve paired weight-search
comparisons. All four expected adaptive wins, four losses and four null pairs
matched; those counts include the nested repeats. The eight predeclared nonzero
adaptive/fixed-order practice regrets also matched. Seeded-random results and
regrets were retained and verified without selecting favorable seeds.

The measured informative instances gave adaptive/fixed weight scores of
8/8 versus 6/8 and 8/8 versus 4/8. The misleading instances gave 6/8 versus 8/8
and 4/8 versus 8/8; neutral scores tied at 4/8 and 2/8. These are deliberately
constructed calibration outcomes, not a population-wide advantage claim.

Context prefixes and top-k-one decoding remained neutral anchors. The unchanged
C/W/D gate therefore vetoed all 36 searches and launched **no audit batches**.
Theoretical audit scores remain unmeasured. M18 retains the positive-audit
regression; M19 preserves the existing 0.1 minimum-gain threshold.

Independent read-only verification checked 48 parent chains,
7,263 record envelopes, 1,644 completed operations
and all **984 observed/reaped CPU inference batches**.
All 7,872 raw inference outputs, 588
full score objects, search histories, prefix curves and all three policies'
practice regrets matched the pinned oracle. No failed, uncertain, blocked or
unstarted rows remained. No attempt was retried or replaced.

Guarded replay preserved 30 bundle files and 8,320 campaign files.
The declared attempt and replay took 1589.90 seconds; observed
worker durations sum to 272.19 seconds and are not total campaign cost.
Search reservations retained 7,488 forward calls;
verified completed search usage was 6,048 calls.
References separately charged and used 1,824 calls.
Every resource meter is retained in the results artifact.

Report digest: `fc34331e612634153057c292694d5977290634d3a63c490f81773fbb3183beba`.
Oracle SHA-256: `6e8199cc21b72da2d3c04139a906893d6910ef45fb8af349b18589c969bb5cef` (1,753,087 bytes).

Raw journals and the independent audit remain at
`/private/tmp/autocontext-m19-linux-evidence/`; macOS JUnit and log files remain at
`/private/tmp/autocontext-m19-macos-tests.xml` and
`/private/tmp/autocontext-m19-macos-tests.log`. Raw attempts are retained outside
Git. Every validated source file is individually pinned.

## Remaining gaps

Native Linux/amd64 acceptance and PR CI are pending. The dedicated workflow
requires one fresh CPU-only attempt, retains failure evidence, and checks exact
calibration and process cleanup. Supplemental ARM64 execution does not close
that native acceptance gap.

These are constructed teachers with nuisance-only role separation. Two task
instances and nested repeats do not establish natural-task transfer or adaptive
superiority. Neutral C/D behavior does not calibrate a general decoding search.
Resource enforcement is scoped to the trusted fixed CPU model, not arbitrary
untrusted programs. Parent-observed process peak RSS was unavailable for every
inference batch. Enforced address-space limits and worker telemetry remain
separate from that measurement.

GPU inference, Qwen base-versus-patch benchmark reproduction, TypeScript runtime
parity and promotion remain deferred.
