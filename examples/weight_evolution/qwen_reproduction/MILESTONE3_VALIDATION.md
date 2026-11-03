# Milestone 3 validation

Validated on 2026-11-02 in branch `codex/qwen-reproduction-report`, based on
milestone 2 commit `bbe26596639fdeeccf8cb104876ccaf816ee8e3c`. No GPU inference or
benchmark model scoring was run. Synthetic worker outputs are explicitly labeled
fixture evidence, including when scored by real official checkers.

## Real evaluator assets

- The explicit dataset downloader fetched and independently verified 1,403,413
  bytes: 500 MATH-500, 1,319 GSM8K and 541 IFEval rows. Ordered normalized cohort
  digests match [datasets.lock.json](datasets.lock.json).
- All 1,319 official GSM8K JSONL question/answer rows were independently compared
  with the pinned Hugging Face test parquet and match in order. Details and
  provenance are in [DATASETS.md](DATASETS.md).
- The scorer downloader fetched five pinned source/resource assets, followed by
  successful offline byte verification. A separate Python 3.13.1 environment
  installed the 21 exact packages using artifact hashes.
- Real scorer preflight accepted every math gold answer and every IFEval
  instruction row. All 25 official IFEval instruction families were exercised
  with synthetic responses, along with symbolic math equivalence, incorrect
  answers and missing-box cases. See [SCORERS.md](SCORERS.md).
- The two final published aggregate files were decoded from GitHub content
  responses, checked against their Git blob identities and pinned by raw SHA-256.
  They are reference values, never represented as autocontext measurements.

The dataset lock digest is
`3fe15fc76a2ce1c53a1f154d326c90f511b1d44323e832807d2b9bef2ae7ebd1`.
The scorer lock digest is
`fd82321ca000fd99f4576c43d396969f16c0902060c4b856a1f418c172e845bb`.

## Frozen planning and report replay

The new `plan-replay` command used the real milestone-2 base/patch checkpoints,
independently reverified their tensor contents, and produced a complete paired
plan for all three datasets. Defaults produce 592 worker shards and reserve at
most 374,865,920 generated tokens and 1,065,600 worker-seconds. Planning started
no model or container. Its default report-disk ceiling is 39,728,447,488 bytes.

Optional CPU integration tests replay synthetic raw evidence through the actual
locked math verifier and official IFEval registry. Both JSON and Markdown reports
were generated; repeated evaluations have identical content digests and retain
the fixture label. Published discrepancies are suppressed for this synthetic
evidence. The real report pipeline streams raw outputs by shard, checks complete
coverage and identities before grading, and detects changes between passes.

## Checks

From `autocontext/`:

```bash
.venv/bin/pytest tests/test_weight_*.py \
  tests/test_docker_watchdog_ownership.py tests/test_docker_kernel_worker.py \
  tests/test_protected_kernel_authority_runner.py tests/test_kernel_generation_campaign.py \
  tests/test_kernel_benchmark_contract.py tests/test_training_statistical_confirmation.py
.venv/bin/ruff check src tests ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/mypy src ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/python ../scripts/check_markdown_links.py
```

The selected regression run passed **733 tests**, with **7 skipped**. Two skips
are the opt-in official-scorer report integrations; both were separately run and
passed with the pinned dependencies and verified resources. Repository-wide Ruff
and mypy passed (972 source files), as did local Markdown links and
`git diff --check`. The complete repository pytest suite and TypeScript runtime
tests were not run; Python-first parity remains explicit.

The new tests cover source/resource tampering, cohort identity and membership,
every shard budget, missing/duplicate/surplus evidence, raw output preservation,
smoke/fixture labeling, paired statistics, failed dispatches, durable pre-dispatch
claims, crash reconciliation, changed harnesses, and no automatic ambiguous retry.

## Remaining reproduction gaps

The pinned Docker image still needs real reconstruction/inference acceptance on
a compatible Linux/amd64 NVIDIA host. A complete GPU base-versus-patch run and
measured discrepancy report remain outstanding. The code and CPU evidence do not
establish the behavioral effect reported by the authors.

The declared official test snapshots are not proven identical to the authors'
private prepared files. The original IFEval wrapper and complete runtime were
unreleased; selected dependency versions, deterministic checker settings and
contiguous shard batching are documented reproduction choices. Practice and
confirmation cohorts, 13-word overlap/contamination audits, editing-agent
discovery, comparison conditions and promotion remain later milestones. The
development runner does not attest exclusive GPU-memory ownership or remotely
attest its execution receipts.
