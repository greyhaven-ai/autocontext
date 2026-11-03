# Milestone 7 validation — adaptive offline editing

Validated locally on 2026-11-02 on Apple Silicon/macOS with Python 3.13.1 and
the locked development environment. Worktree: `qwen-adaptive-editing`; branch:
`codex/qwen-adaptive-editing`. Base: milestone-6 commit
`8d04f333d93e99ffd082469af4ac7092a54a2dfd`
([draft PR #1487](https://github.com/greyhaven-ai/autocontext/pull/1487), stacked
on [M5 PR #1486](https://github.com/greyhaven-ai/autocontext/pull/1486)).
This validation describes the milestone-7 change stacked on milestone 6.

## Implemented behavior

- Existing `LLMProvider`, `CompletionResult`, `OutputSchema`, and
  `build_refinement_prompt` interfaces drive strictly offline, feedback-addressed
  proposal catalogs. Local validation retains actual constraint metadata and
  rejects malformed, truncated, oversized, no-op, or wrong-surface proposals.
- Proposal calls run inside M6's claimed inspections. Expanded provider input
  is checked before dispatch; raw output and normalized results must fit their
  reservations. No live provider is accepted.
- C/W/D share frozen rounds, limits, and per-operation reservations. Complete
  practice correctness selects the winner, with earliest-round ties. Earlier
  winners are restored and re-probed under the same budget before sealing.
- Search, restoration, failures, and incomplete dispatches retain M6 journal
  accounting. Exact replay checks source, lineage, selection, terminal markers,
  and complete/sealed evidence without new provider/probe calls.
- A separate one-look confirmation binds all sealed search receipts, their
  baseline/scorer contracts, its held-out cohort, policy and exact evidence.
  A veto cannot trigger reselection and prevents the final audit. An all-pass
  fixture reaches the existing evaluator only after exact audit-cell matching.

Independent reviews found and resolved baseline/scorer substitution in the
standalone confirmation API, ignored terminal-failure markers, and broken
symlinks that could evade the veto branch's forbidden-audit-file check. The
README uses fresh relative paths so strict symlink rejection also works on
macOS, where `/tmp` itself is a symlink.

## Automated validation

Run from `autocontext/`:

```bash
.venv/bin/pytest tests/test_weight_*.py \
  tests/test_docker_watchdog_ownership.py tests/test_docker_kernel_worker.py \
  tests/test_protected_kernel_authority_runner.py tests/test_kernel_generation_campaign.py \
  tests/test_kernel_benchmark_contract.py tests/test_training_statistical_confirmation.py -ra
.venv/bin/ruff check src tests ../examples/weight_evolution/qwen_reproduction/run.py \
  ../examples/weight_evolution/qwen_comparison ../examples/weight_evolution/qwen_sessions \
  ../examples/weight_evolution/qwen_adaptive
.venv/bin/mypy src ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/mypy ../examples/weight_evolution/qwen_adaptive
.venv/bin/python ../scripts/check_markdown_links.py
git diff --check
```

The selected regression suite passed **1,345 tests**, with **7 skipped**, in
50.03 seconds. Skips require explicit official scorer assets/environment (2),
real Docker/MIG (1), or native Windows launcher/Job Object behavior (4). These
skips provide no GPU acceptance evidence.

The 142 new tests comprise 54 provider/adapter cases, 31 adaptive controller
cases, 34 confirmation cases, and 23 end-to-end workflow cases. They cover
feedback-sensitive routing, local schema enforcement and metadata, transformed
provider byte limits, earlier-best/tie selection, failed generation/probes,
equal worst-case budget preflight, ambiguous dispatch blocking, restoration,
exact resume, source drift, terminal-marker/selection tampering, hidden-data
exclusion, confirmation pass/veto, baseline binding, one-look evidence identity,
and audit gating. Tests reject network/subprocess execution in the offline path.

Ruff passed. Mypy passed for 989 source/reproduction files and the two M7 example
files; the two M6 example files were also checked. TypeScript runtime tests were
not run because execution parity remains deferred. This validation uses selected
regressions rather than the entire repository test suite.

## Repeated end-to-end runs

Preflight succeeded. Two independent default runs and an exact resume produced
**126 byte-identical files**. There were 33 total session operations: 12 for C,
9 for W, and 12 for D, including one charged provider failure and one charged
probe failure. All three selected round two on practice evidence. C/D restored
that earlier state; W's failed third proposal left the chosen state current.

Default confirmation outcomes were C confirmed, W confirmed, D vetoed. D's
practice winner regressed on the separate toy GSM8K confirmation question; no
audit directory or plan was emitted. A separate all-pass fixture completed the
60-cell audit after confirmation. Replacing veto evidence with passing evidence
in the same output is rejected.

| Artifact | Digest |
| --- | --- |
| Default workflow | `60c6c6e5b799dd2e17bd19d998f77e6e44b5fb4a7ce7db05d8600b89268dc0fb` |
| Default confirmation receipt | `f9d36f83c2b383f24e16636280adf6bc73432de68ec11745ae48ee4e19696d3a` |
| Separate all-pass workflow | `e233f83c1e754696981e6f47741c5280836200a17b3f1eae5624c359b9f0b89d` |
| All-pass audit report | `1fc6a1ae371c669b4ab148f67c465d0c842997d38ae2fd8f40c92bf5bc61df1b` |

## Limits and remaining reproduction work

No GPU, Docker worker, API model call, real tensor-editing program, downloaded
benchmark data, or paid infrastructure ran. Literal routes exercise feedback
dependence; they do not show that an LLM learned or discovered a better edit.
W changes only a synthetic descriptor. Byte reservations and simulated seconds
are not token costs, measured latency/GPU usage, or enforced wall-clock time.

The confirmation threshold is descriptive on three synthetic questions, not a
statistical confidence or promotion guarantee. The final audit is also a toy
prerecorded catalog. All outputs retain fixture/no-model/no-promotion claims.
Search and held-out roles have logical API separation for trusted local code;
they are not an adversarial sandbox, remote attestation, or a defense against
replacing an entire experiment and its trusted inputs.

Real candidate generation and tensor edits, inference-backed probes, hard
process/resource enforcement, benchmark practice/confirmation selection and
contamination review, independent repeated-search trials, GPU acceptance and
full base-versus-patch reproduction remain open. The original hardware/runtime,
official scorer, published-score and reproduction gaps remain documented in
[M4 validation](../qwen_reproduction/MILESTONE4_VALIDATION.md).
