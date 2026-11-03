# Milestone 6 validation — bounded CPU editing sessions

Validated locally on 2026-11-02 on Apple Silicon/macOS, using Python 3.13.1 in
the locked development environment. The worktree is `qwen-editing-sessions`,
branch `codex/qwen-editing-sessions`, based on milestone-5 commit
`bd7885ce1e36a9759483647d49b8b1cb47e97adc` ([draft PR #1486](https://github.com/greyhaven-ai/autocontext/pull/1486)).
This validation describes the milestone-6 change stacked on milestone 5.

## Implementation

Typed finite C/W/D editing sessions reuse the existing comparison artifacts,
synthetic scorer, bounded JSON/file readers, advisory locks, and immutable
publication patterns. The controller owns candidate transitions and scoring.
Trusted scripted adapters receive practice projections only. Claims precede
dispatch; failed attempts retain reservations; incomplete dispatches block
automatic retry. Published results reconcile without redispatch. Exports seal
the current probed revision and derive costs/source IDs from its verified journal.

The example freezes synthetic practice/confirmation/final-audit roles, literal
adapter scripts, operation lineage, budgets, and the M5 source plan/evidence.
Three sessions feed the existing five-condition evaluator. Reusing recorded
audit responses requires exactly identical cell digests; altered candidate
contents cannot inherit those responses.

Independent review found and resolved four issues before validation:

- Crash-leftover staging files prevented otherwise valid result reconciliation.
- Valid per-counter reservations could sum above the M5 export cost cap.
- Workflow comparison-search limits were checked only after session dispatch.
- Python JSON-value equality could accept boolean/integer type substitutions.

## Automated checks

Run from `autocontext/`:

```bash
.venv/bin/pytest tests/test_weight_*.py \
  tests/test_docker_watchdog_ownership.py tests/test_docker_kernel_worker.py \
  tests/test_protected_kernel_authority_runner.py tests/test_kernel_generation_campaign.py \
  tests/test_kernel_benchmark_contract.py tests/test_training_statistical_confirmation.py -ra
.venv/bin/ruff check src tests ../examples/weight_evolution/qwen_reproduction/run.py \
  ../examples/weight_evolution/qwen_comparison ../examples/weight_evolution/qwen_sessions
.venv/bin/mypy src ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/mypy ../examples/weight_evolution/qwen_comparison
.venv/bin/mypy ../examples/weight_evolution/qwen_sessions
.venv/bin/python ../scripts/check_markdown_links.py
git diff --check
```

The selected regression suite passed **1,203 tests**, including **210 new session
tests**, with **7 skips** in 23.48 seconds. Skips require the explicit pinned
official scorer environment/assets (2), real Docker/MIG release-gate setup (1),
or native Windows launcher/Job Object behavior (4). They are not passing GPU
acceptance evidence. This was the selected regression suite, not all repository
tests.

Ruff passed. Mypy passed for 985 source/reproduction files and for the comparison
and session examples separately (two files each). Repository-wide TypeScript
tests were not run; TypeScript execution parity is deferred.

Coverage includes strict schema/pin loading, split overlap/alias rejection,
hidden-data access rejection, exact edits and parent lineage, known failures vs.
zero scores, all cumulative limits, changed source/scorer/result/receipt rejection,
concurrent dispatch once, unresolved claim blocking, published-result recovery,
seal behavior, ledger-derived exports, exact-cell evidence reuse, no network or
subprocess execution, deterministic rebuild, and no adapter calls on resume.

## End-to-end offline evidence

`run.py validate` succeeded without creating sessions. Two fresh `run-fixtures`
executions and an exact resume produced **49 byte-identical files**. Each run
contained 12 claimed operations, three charged known failures, three sealed
exports, and 60 comparison cells across A/C/W/CW/D. The successful W probe's empty
answer scored zero; it was not converted into an infrastructure failure.

| Identity | SHA-256 |
| --- | --- |
| Workflow | `65dd75cab887230e3aa6c18e8e939bd38ffeba26f4941d45b9b0c94918b565d2` |
| Comparison plan | `4ad97a261cafebc2c25b73434570ea13d362b2eb129c1a282fea7588ab8f0c11` |
| Comparison report | `1f9e1d82cffc9c443c1a54c997ecfcbffbe153a354121c0151140a705e808b74` |

Each exported candidate reports four search calls and 98,304 conservative
reserved byte units, including the failed probe. These costs are fixture units,
not token costs or GPU seconds. Confirmation remains committed but unused.

## Remaining acceptance and reproduction gaps

No GPU, Docker worker, Qwen inference, real weight-editing program, model-backed
search, or paid infrastructure ran. W edits a synthetic descriptor. The
in-process scripted adapter is trusted; it has no hard execution timeout or
untrusted-code isolation. Source hashes and local journal checks detect drift
and tampering relative to the retained records; they are not remote attestation
or a defense against replacing the entire experiment and its trusted inputs.

Real benchmark practice/confirmation selection and contamination review remain
open, as do inference-backed probes, real candidate generation, repeated-seed
search comparisons, and confirmation/promotion decisions. Existing official
scorer, GPU acceptance, full base-versus-patch reproduction, hardware/runtime
equivalence, and published-score gaps remain in
[milestone-4 validation](../qwen_reproduction/MILESTONE4_VALIDATION.md).
No benchmark gain, retained learning, promotion, or reproduction success is
claimed by the fixture reports.
