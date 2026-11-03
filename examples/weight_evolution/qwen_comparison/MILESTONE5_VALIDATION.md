# Milestone 5 validation

Implemented on 2026-11-02 in isolated branch `codex/qwen-comparison-harness`,
based on milestone 4 commit `fdf8094e6f0ff557e3daeda82b2ab4bf2f0b6847`.

**GPU execution remains deferred.** No model, optimizer, inference service or
container was run. The completed scope is the comparison-condition contract,
offline fixture scorer/evaluator and reproducible example reports.

## Contract and implementation

The frozen matrix contains A (baseline), C (context only), W (weights only),
CW (exact C/W composition) and D (decoding control). The planner derives and
revalidates each condition's permitted artifacts and a common problem/seed/sample
schedule. Human artifact labels are separate from content identity. Request
projections contain no gold answer or scorer instruction payload.

Development and evaluation IDs/normalized prompts must be disjoint; normalized
aliases within either split also fail. Candidate provenance may reference only
the common practice pool. Frozen budgets cover input/output fixture units,
declared elapsed values and mock search usage. CW attributes C+W costs while
counting their shared discovery only once in the study total.

The evaluator authenticates exact cell coverage and response identities before
grading, preflights every scorer input and preserves wrong/empty/cap-limited
answers as scored outcomes. It reuses the existing boxed-answer/thinking
boundaries, grouped sample accuracy and paired-statistics helper. Model/scorer
infrastructure errors cannot become score-zero observations.

The new comparison schema is separate from the strict two-variant reproduction
schema. No M1–M4 Python implementation, reproduction/runtime/data/scorer lock,
core dependency, CI or production CLI/MCP surface was changed. Documentation
adds pointers to this Python-only example; TypeScript parity remains deferred.

## Actual offline fixture replay

The checked-in bundle contains three synthetic practice problems, six synthetic
audit problems, seeds 0 and 17, one sample per seed/problem and five conditions:
60 cells. The scorer-route names do not establish benchmark membership. Both
checkpoints are text descriptors, and the candidate context/decoding artifacts
are hand-authored stand-ins with declared mock provenance.

`build_fixture.py` reproduces the pinned bundle byte for byte from a literal
response catalog separate from gold-based scoring. The bundle lock authenticates
72,435 study bytes and 31,006 response bytes. Its plan digest is:

`bb02b2ea61709cc010b9722b156e85e2a2be67700acfd042ea633930486fe603`

The example's `validate` and `run-fixtures` commands succeeded without model or
official-grader dependencies. Two independent report publications produced
identical JSON, Markdown and completion-marker bytes. Completion-marker hashes
were independently checked against their files. The report content digest is:

`4308d07ae19829a73d1da08d14787d5cd01777dd197290ddaa0e86ea290ae998`

The catalog exercises correct/wrong math, empty responses, a correctly boxed
answer at the output cap, incomplete thinking, strict instruction failures,
condition regressions and negative factorial interactions. These outcomes are
chosen fixtures, not observations about Qwen or any optimization method.

The output cap is 96 UTF-8 byte units, reserving 1,152 units per condition and
5,760 overall. Reports distinguish those units and mock elapsed/search values
from measured model tokens, wall time, optimization calls or monetary costs.
Every report explicitly denies model execution, benchmark, promotion and
retained-learning claims.

## Verification

From `autocontext/`:

```bash
.venv/bin/pytest tests/test_weight_*.py \
  tests/test_docker_watchdog_ownership.py tests/test_docker_kernel_worker.py \
  tests/test_protected_kernel_authority_runner.py tests/test_kernel_generation_campaign.py \
  tests/test_kernel_benchmark_contract.py tests/test_training_statistical_confirmation.py
.venv/bin/ruff check src tests ../examples/weight_evolution/qwen_reproduction/run.py \
  ../examples/weight_evolution/qwen_comparison
.venv/bin/mypy src ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/mypy ../examples/weight_evolution/qwen_comparison
.venv/bin/python ../scripts/check_markdown_links.py
```

The selected regression suite passed **993 tests, with 7 skipped**, including
133 new comparison tests. Repository-wide Ruff passed. Mypy passed for 980
source files including the reproduction example, and separately for both new
comparison example scripts. Separate invocations avoid the examples' duplicate
`run.py` module name. Local Markdown links and `git diff --check` passed.

Tests cover arm permissions, nontrivial interventions, stale identities,
duplicate/normalized-alias cases, practice-only provenance, gold-free requests,
schedule size limits, matched seeds/samples, fixture byte budgets, missing/extra
or mislabeled observations, scorer failures, report hashes and immutable fixture
labels. Example tests forbid network/process creation during replay and reject
GPU/execute flags. Fixture regeneration is deterministic and never overwrites an
existing bundle.

The seven skips are existing opt-in integrations. The full repository pytest
suite, TypeScript runtime tests and official scorer integration tests were not
run for this milestone. New scoring tests use explicit toy checkers, not the
official benchmark graders.

## Remaining scope

No real C*/W*/D* search, tensor patching, target-model adapter, metered editor/probe
session or comparison GPU executor is implemented here. Genuine independent
artifacts, official grading, real token/batch/cost measurements and frozen
held-out comparisons require subsequent work. Practice provenance checks cannot
prove an author avoided hidden answers or that a model lacks pretraining
contamination; the broader contamination audit remains outstanding.

Paired intervals are descriptive and use problems as blocks after averaging
seeds/samples. They do not provide simultaneous inference, reliable estimates
across editing runs, noninferiority/promotion gates or evidence of retained
learning. Joint optimization and retained-memory on/off need separate study
conditions and budgets. M4 live GPU acceptance and full published-patch
reproduction remain pending and are not dispatched by this fixture workflow.
