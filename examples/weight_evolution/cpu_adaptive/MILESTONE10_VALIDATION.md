# Milestone 10 validation — adaptive CPU search

Implemented on 2026-11-03 in isolated worktree `cpu-adaptive-search`, branch
`codex/cpu-adaptive-search`, based on merged main
`10663af82028d1abaeb0b10df6b5fbe4449e21d6` (M9 / PR #1495).
Local validation uses Apple Silicon/macOS, Python 3.13.1, and locked dependencies.
GPU execution remains deferred.

## Scope

Three new modules add separate CPU search contracts, a deterministic practice
policy, and a durable coordinator. The search uses the existing tiny F32 model,
verified safetensors edits and registry, bounded CPU runner, and neutral journal
publication/locking helpers. M1–M8 fixture sources and pins retain their meaning.
M9's bundle is refreshed because its worker now exposes the same resource-limit
installation to direct Linux acceptance probes.

The finite input bundle declares five checkpoints, four C/W/D options each,
three trials per arm, the scorer, practice/confirmation/audit cases, fixed
evaluation seed 406, and source hashes. It contains no response catalog.
The local policy receives measured practice counts, continues an improving
direction, and otherwise changes direction. Scores never come from option IDs.
All model outputs are fresh forward passes through independently verified bytes.

Selection includes the baseline, uses complete practice coverage, and retains
the earliest candidate on a tie. Charged restoration reproduces the selected
candidate's logits and tokens. Native seals and the selection precede one-look
confirmation; a veto withholds audit without reselection. CW composes selected
C and W and receives no additional search.

## Measured behavior

| Arm | Baseline and three practice trial counts | Selected option |
| --- | --- | --- |
| C | 1, 3, 2, 3 correct out of 5 | `c-hint-1` |
| W | 1, 2, 2, 0 correct out of 5 | `w-row-two-1` |
| D | 1, 5, 4, 1 correct out of 5 | `d-temperature-1` |

Changing one practice label changes the measured score of W's first edit and
switches the second proposal to a different direction, without changing options
or their order. That controlled case retains the base checkpoint and vetoes audit.
The default confirmation and audit scores are A 1/5, C 3/5, W 2/5, CW 4/5,
and D 4/5. These deliberately constructed, correlated cases demonstrate execution
correctness, not independent generalization or benchmark improvement.

Each C/W/D arm receives three proposals and 25 forward passes, including the
baseline and restoration. Their shared input-token capacity is 75; actual
input tokens are C 50, W 25, D 25. Five materializations are charged separately
before search. The default run consumes nine proposals, five materializations,
130 forward passes, 130 output tokens and 255 input tokens in 26 worker batches. The
confirmation-veto bundle consumes 105 forward passes/output tokens and 200 input
tokens in 21 batches; it publishes no audit report.

Two fresh CLI runs produced identical semantic receipt hashes and byte-identical
comparison reports. Exact resume preserved all 110 default output files and
all 99 veto output files. Rebuilding the three-file input bundle reproduced
every byte. The default run's maximum measured worker RSS was 40,189,952 bytes;
summed worker elapsed time was about 2.76 seconds. This local timing observation
is not a performance comparison. Full receipt hashes retain telemetry separately
from deterministic semantic hashes.

## Linux resource acceptance

The production worker's stdlib-only `_install_resource_limits` installs and
verifies address-space, CPU and zero core-dump limits before importing the model.
Acceptance tests invoke that exact helper through the existing parent supervisor:

- A 512 MiB allocation under a 256 MiB `RLIMIT_AS` fails with `MemoryError`
  and an explicitly unsuccessful exit; it cannot become a successful receipt.
- A busy worker with a one-CPU-second hard limit receives kernel `SIGKILL`
  before the independent ten-second parent watchdog.
- A sleeping worker with a 30-second CPU allowance exceeds a one-second parent
  wall deadline and is killed and reaped.
- The inherited-parent-RSS regression still passes; Linux telemetry uses
  post-exec `VmHWM` while `RLIMIT_AS` supplies the hard bound.

The focused resources/runner suite passed **59 tests**, with one Darwin-only
skip, in a network-disabled Linux/arm64 Docker container. It used one CPU,
768 MiB container memory, a read-only worktree, and image
`python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e`.
Locked dependencies were installed from pre-downloaded wheels. No model API,
GPU backend or GPU device was used. This validates the trusted worker boundary;
it does not establish isolation for arbitrary code or all Linux configurations.

The expanded Linux suite passed **305 tests**, with one Darwin-only skip, in
**79.44 seconds** under the same container controls and a 180-second outer test
deadline. It covers all eight CPU test modules: model, resources, runner, study,
search contracts, policy, coordinator, and independent replay review. It includes
actual CPU search, both confirmation outcomes, and normal and interrupted replay.

## Replay and failure checks

Every materialization, proposal and inference receives a durable reservation
before execution. Failed operations retain failure records; ambiguous operations
retain incomplete claims. Both remain charged and block automatic retry.
Resume replays the entire retained prefix before new work, verifies source,
runtime, checkpoint, receipt, decoder, resource, scorer, selection and gate
bindings, and rejects unowned derived evidence. Independent review found an
unclaimed-materialization adoption gap; the fix compares all retained checkpoint
outputs to verified materialization claims before dispatch. Regression tests
cover empty and baseline-only journal prefixes with unexplained future outputs.
Two additional checks verify legitimate recovery: a prefix through C restoration
executes only the 21 remaining batches, and a completed materialization result
can publish its missing seal without materializing again. Retained bytes and
final semantic results remain unchanged.

Semantic replay does not claim remote attestation or protection against replacing
an entire experiment and all provenance. The policy is a bounded local search
algorithm, not an LLM proposal generator.

## Validation commands

Run from `autocontext/`:

```bash
uv sync --locked --group dev
.venv/bin/pytest tests/test_weight_cpu_*.py -m 'not live' -ra
.venv/bin/pytest tests/test_weight_*.py tests/test_dict_type_consistency.py \
  tests/test_docker_watchdog_ownership.py tests/test_docker_kernel_worker.py \
  tests/test_protected_kernel_authority_runner.py tests/test_kernel_generation_campaign.py \
  tests/test_kernel_benchmark_contract.py tests/test_training_statistical_confirmation.py -m 'not live' -ra
.venv/bin/ruff check src tests ../examples/weight_evolution/cpu_adaptive ../examples/weight_evolution/cpu_inference
.venv/bin/mypy src
.venv/bin/mypy ../examples/weight_evolution/cpu_adaptive
.venv/bin/mypy ../examples/weight_evolution/cpu_inference
.venv/bin/python ../scripts/check_markdown_links.py
git diff --check
```

The new contracts/policy suite passed 72 tests and the example/coordinator suite
passed 44 tests. The final local CPU suite passed **302 tests**, with four
Linux-only skips, in **35.15 seconds**. Ruff passed; mypy passed for 1,000 source files and both example
directories (two files each). The broader selected regression suite passed
**1,790 tests**, with **11 skipped**, in **224.41 seconds**. The skips require
Linux resource/RSS behavior (4), official scorer assets/environment (2), real
Docker/MIG (1), or native Windows behavior (4). Two resume regressions added
after that run passed in the complete four-test independent-review suite and
the expanded Linux suite. No full-repository or TypeScript suite was rerun locally.
Markdown validation passed for 439 local links across 166 files, and
`git diff --check` passed. The Linux validation's 22 source/test/bundle hashes
were independently rechecked against the final worktree.

## Artifact identities

| Artifact | SHA-256 / digest |
| --- | --- |
| Base checkpoint | `8769486603c36aced642b933fde7ba200d2efba3d60a1fbc25e2fe551c85bfd9` |
| Default study | `7226f2cdf95f17196806be02f9081c84f9e68de214f4a88d10f0f2514d0abf4a` |
| Veto study | `7f82962fef6946d98a2a0aec239e3b77f3c3a30b736a903b0032e573203f5d45` |
| Local runtime | `f4c588e1e9586095b280e2604d6b414ad7db181db6f602ff45426dda19089b00` |
| Default semantic summary | `fad618d314511aad4566e0b1c19de25c0cc5f62ba0bb7f08cd74e51f265125d4` |

## Remaining work

Qwen inference and full checkpoint editing, GPU acceptance and published-score
reproduction, live model proposals, open-ended edit programs, independent
benchmark splits and contamination review, repeated-search quality studies,
and TypeScript execution parity remain deferred. Linux/amd64 CI on this change
has not run locally; macOS still uses monitored RSS with possible sampling
overshoot rather than a hard address-space limit. No performance or promotion
claim is enabled by these constructed-task results.
