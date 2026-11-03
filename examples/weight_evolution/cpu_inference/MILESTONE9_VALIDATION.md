# Milestone 9 validation — actual CPU inference

Validated locally on 2026-11-03 on Apple Silicon/macOS with Python 3.13.1,
using the locked development environment. Worktree: `cpu-checkpoint-inference`;
branch: `codex/cpu-checkpoint-inference`; base: merged `main` commit
`e115c1842a51bf80cf78c9c9b00c9667ee8c9eb7` (milestone 8).

## Scope and evidence

The five new modules define strict CPU model/request/result contracts, a trusted
stdlib F32 token-sum classifier, a bounded subprocess worker, and a durable
comparison coordinator. All model outputs come from verified safetensors bytes.
There are no prerecorded response catalogs, model APIs, downloaded models,
Torch/MLX inference backends, or GPU execution paths. The checkpoint builder
reuses the earlier data-only tensor reader/writer, edit contracts, and registry.
M1–M8 source and fixtures remain unchanged.

The three-file input bundle pins a 240-byte, two-tensor checkpoint, complete
vocabulary and architecture, two fixed sparse edit programs, decoding, all cases
and labels, scorer source, budgets, and implementation hashes. The worker gets
no labels or scorer authority. Runtime identity pins interpreter bytes/version,
platform, validation dependencies, source closure, and resource policy at launch.

Candidates are fixed before inference. Practice is diagnostic and does not
choose another candidate or drive adaptive search. Five token families use
one/two/three repetitions across practice/confirmation/audit. These are deliberately
constructed, correlated inputs, not independent benchmark or generalization data.

| Condition | Practice correct | Confirmation correct | Audit correct |
| --- | --- | --- | --- |
| A — base | 1/5 | 1/5 | 1/5 |
| C — context prefix | 3/5 | 3/5 | 3/5 |
| W — helpful edit | 2/5 | 2/5 | 2/5 |
| CW — context and weight | 4/5 | 4/5 | 4/5 |
| D — sampled decoding | 5/5 | 4/5 | 4/5 |

Controlled checkpoint checks measured base 1/5, helpful edit 2/5, harmful edit
0/5, and restored base 1/5. Restoration matches both logits and output tokens.
The default study reserves and executes 95 forward passes, 200 input tokens,
and 95 output tokens in 19 subprocess batches. A separate pinned confirmation
cohort vetoes D at zero gain; that experiment stops at 70 forward passes and
145 input tokens without publishing audit evidence. Neither result establishes
that editing improves a language model or solves unseen tasks.

Two fresh CLI runs had identical semantic receipt digests and byte-identical
comparison reports. Exact resume retained all 58 output files byte-for-byte;
the veto resume retained all 47 files. Rebuilding the input bundle reproduced
every byte. Elapsed time and peak RSS are real measurements and deliberately
excluded from semantic digests; complete evidence separately anchors their full
receipt hashes. Fresh runs do not claim byte-identical timing or memory evidence.

## Resource and replay checks

Each batch has a hard five-second parent wall deadline and an OS CPU limit.
Requests, pipes, model dimensions, input tokens, and output tokens have finite
caps. The bundle reserves at most 100 forward passes, 512 input tokens and
100 output tokens, with at most 16 input tokens per request and one output token.
The worker has a 512 MiB memory policy. Linux uses `RLIMIT_AS`; this macOS run
used monitored RSS at 10 ms intervals with explicitly recorded possible sampling
overshoot. It did not establish a hard address-space limit on macOS. The observed
maximum worker RSS was 39,960,576 bytes; summed worker elapsed time was about
1.89 seconds for the first 19-batch run. These are local measurements, not a
performance comparison.

The worker blocks network, child-process, and external model-backend operations.
This is still trusted fixed code with resource controls, not an arbitrary-code
sandbox. Linux hard-address-space enforcement is implemented but has not been
executed on this Mac; it needs Linux CI validation. Windows execution is rejected.

Durable reservations precede dispatch. An incomplete claim remains charged and
cannot silently retry. Resume validates an ordered claim/result prefix before
any execution, rechecks every used checkpoint and native seal, checks the same
request/runtime/token/decoder/resource bindings as fresh responses, and regrades
saved outputs without another forward pass. Missing completed evidence is not
recreated. Confirmation is one-look and fixed-selection; a veto withholds audit.

Independent review found and corrected prefix-gap redispatch, late rejection of
unowned derived files, insufficient saved-receipt validation, and unanchored
telemetry. Regression tests cover each case, along with source/checkpoint/prompt
tampering, literal-type substitution, timeouts, output caps, resource-policy
checks, deterministic decoding, and safe worker cleanup.

## Validation commands

Run from `autocontext/`:

```bash
uv sync --locked --group dev
.venv/bin/pytest tests/test_weight_*.py tests/test_dict_type_consistency.py \
  tests/test_docker_watchdog_ownership.py tests/test_docker_kernel_worker.py \
  tests/test_protected_kernel_authority_runner.py tests/test_kernel_generation_campaign.py \
  tests/test_kernel_benchmark_contract.py tests/test_training_statistical_confirmation.py -m 'not live' -ra
.venv/bin/ruff check src tests ../examples/weight_evolution/cpu_inference
.venv/bin/mypy src
.venv/bin/mypy ../examples/weight_evolution/cpu_inference
.venv/bin/python ../scripts/check_markdown_links.py
git diff --check
```

The focused milestone suite passed **157 tests**: 96 model/contract tests,
31 runner/worker tests, and 30 study tests. Ruff passed; mypy passed for 997
source files and two example files. The full repository and TypeScript runtime
suites were not rerun for this Python-only milestone.

The broader selected regression suite above passed **1,647 tests**, with
**7 skipped**, in 183.50 seconds. It covers all weight-evolution tests, dictionary
annotation conventions, Docker watchdog/worker ownership, protected kernel
authority, kernel generation/benchmark contracts, and statistical confirmation.
The skips require explicit official scorer assets/environment (2), real
Docker/MIG (1), or native Windows launcher/Job Object behavior (4); none is GPU
acceptance evidence. Markdown validation passed for 431 local links across
164 files, and `git diff --check` passed.

## Artifact identities

| Artifact | SHA-256 / digest |
| --- | --- |
| Base checkpoint | `8769486603c36aced642b933fde7ba200d2efba3d60a1fbc25e2fe551c85bfd9` |
| Helpful checkpoint | `bde0dd60d7c20f728e88155f08223146d1cfbe4bc80cab7e10ef8cd51b7d69b4` |
| Harmful checkpoint | `fe3798e81533637bdfe304b021233d85cfc6129ffc05c16ad4a810bf04e25c7c` |
| Study | `2b54309dd5faf2cd9f012a1c204937c3700cb9d1a95d39d8a051b80716dd7397` |
| Local runtime | `5b4bab8759a83c21fc2adff110e484d1f4bae6dde16b8bb072b90f76d6d81abc` |
| Semantic summary | `1fb37b85bd150e6530eaed6dbdabf6ec2e159b8b3f9738f6ab284037d1f7e8fb` |

## Remaining work

Inference-driven adaptive proposal generation/selection, full-sized Qwen,
real benchmark splits and contamination review, repeated-search studies,
TypeScript parity, Linux resource integration validation, GPU acceptance and
published-score reproduction remain deferred. The local hashes authenticate
retained trusted inputs and records; they are not remote attestation or a defense
against replacing an entire experiment and its provenance.
