# GridCTF policy reuse pilot — September 21, 2026

**Decision: go for a further scoped reuse experiment; no production promotion.**
The frozen reusable policy met the predeclared quality and cost criteria in this
one-step simulator. The result supports amortizing repeated model calls when a
verified action contract remains stable. It does not establish useful general
reasoning, complex game play, or state-dependent policy intelligence.

## Protocol and execution

[Protocol](protocol.json) and the measurement harness were committed before
inference (`ab4d93d58`). The initial `gpt-5.4-mini` request was rejected by the
subscription endpoint. Before any successful inference or held-out evaluation,
commit `316623d96` recorded the switch to `openai-codex/gpt-5.5`, low thinking,
and retained the failed attempt in setup accounting. The 28-invocation cap was
unchanged. The completed run used **23 invocations: 22 completed, one rejected**,
and **24,010 reported tokens** (8,064 input, 15,946 output, no cache tokens).

Training used seeds 0–3 and at most three evaluated candidates through the existing
`PolicyRefinementLoop` and isolated `PolicyExecutor`. There were 16 training
evaluations including the four from the aborted setup. The selected policy was
frozen at `2026-09-21T18:58:00.112980Z`, before the 12 held-out seeds 101–112 and
four shifted seeds 201–204. No held-out result influenced candidate construction.
Arm order rotated by seed; initial-state hashes matched across all three arms.
Both the synthesizer and per-action model received the public scoring formula.

The quality floor was 0.75; acceptable mean regression was 0.02, with a paired
2,000-resample seed bootstrap. Each episode allowed one action and 90 seconds;
model actions allowed at most two attempts. Application and provider retries
were disabled in Pi except for the explicitly budgeted invalid-action retry.
No invalid-action retries were needed. Pi does not enforce an API output-token
cap; the predeclared 100,000 observed-token stop threshold was not approached.

## Held-out results

Runtime costs below exclude setup, which is included in the amortization tables.
Latency includes CLI startup, inference, and process overhead for model calls;
policy timing includes isolated child execution. Dollar marginal cost under the
existing subscription is **unknown**, not zero.

| Arm | Mean score | Successful episodes | Runtime model calls | Runtime tokens | p50 latency | p95 latency |
|---|---:|---:|---:|---:|---:|---:|
| Model per action | 0.8928 | 12/12 | 12 | 15,203 | 21.565 s | 31.649 s |
| Reusable policy | 0.8995 | 12/12 | 0 | 0 | 0.00749 s | 0.01015 s |
| Hybrid | 0.8995 | 12/12 | 0 | 0 | 0.00702 s | 0.01596 s |

All arms had zero errors and illegal actions on supported cases. Policy/hybrid
mean score difference from the model was **+0.00671**, with conditional seed
bootstrap 95% interval **[+0.00119, +0.01217]**. This interval is conditional on
one synthesized candidate and these model draws; it does not measure reliability
across synthesis runs or repeated baseline judgments. Twelve observations also
make p95 latency descriptive rather than a dependable service-level estimate.

The final policy is simply a constant action:
`aggression=0.92672, defense=0.47328, path_bias=1.0`. This is an especially favorable
case for reuse. The quality benefit is small, and reuse was worse on seeds 102,
107, and 108 (maximum score loss 0.0051).

## Changed rules and abstention

The shifted contract reduced `aggression + defense` from at most 1.4 to at most
0.8. The local applicability adapter checked the contract before policy execution.

| Arm | Mean score | Successful episodes | Invalidations / abstentions | Runtime model calls |
|---|---:|---:|---:|---:|
| Model per action | 0.8037 | 4/4 | 0 | 4 |
| Reusable policy | 0 | 0/4 | 4 invalidations | 0 |
| Hybrid | 0.8037 | 4/4 | 4 abstentions to model | 4 |

The pure policy's zeros represent explicit non-execution, not illegal actions.
The hybrid matched baseline scores on all four shifted cases, using the current
rules in its fallback prompt. Its fallback frequency was 0/12 on supported cases
and 4/4 on shifted cases. This validates only the small hand-authored applicability
check, not a production router or automatic skill discovery.

## Setup, first use, and amortization

Setup cost was **47.071 seconds, three provider invocations, and 2,908 tokens**,
including synthesis, candidate evaluation, and the rejected model attempt. The
prior failed setup contributed 5.796 seconds measured from its start timestamp
to the final ledger file timestamp. Its four initial-policy evaluations did not
retain individual traces; their total elapsed cost is included, and that logging
limitation is recorded in `prior-attempt.json`.

The first observed supported task cost 47.081 seconds and 2,908 tokens with the
policy, versus 21.974 seconds and 1,299 tokens for model-per-action. Reuse is worse
for first use. Cumulative policy time and tokens first fell below baseline at
**two observed episodes**; using measured mean costs projects the crossover at
**three episodes**. Logical provider invocations first improve at **four**
episodes, including the rejected setup call. These resource crossings are not
invoiced-dollar break-even estimates.

The following horizons are **projections from measured supported-task means**,
not additional executions. Each policy arm pays its full setup cost independently.
The hybrid projections are effectively the same as policy while the contract
stays supported; changed-rule fallback adds model cost.

| Repetitions | Model cumulative seconds | Policy cumulative seconds | Model cumulative tokens | Policy cumulative tokens |
|---:|---:|---:|---:|---:|
| 1 | 21.940 | 47.079 | 1,267 | 2,908 |
| 2 | 43.881 | 47.086 | 2,534 | 2,908 |
| 5 | 109.701 | 47.110 | 6,335 | 2,908 |
| 10 | 219.403 | 47.148 | 12,669 | 2,908 |
| 25 | 548.507 | 47.264 | 31,673 | 2,908 |
| 100 | 2,194.029 | 47.844 | 126,692 | 2,908 |
| 1,000 | 21,940.294 | 54.800 | 1,266,917 | 2,908 |

Actual resource cost per successful episode across all 16 held-out/shifted cases,
including setup and unsuccessful invalidated episodes:

| Arm | Successful episodes | Seconds / success | Invocations / success | Tokens / success | Dollars / success |
|---|---:|---:|---:|---:|---|
| Model | 16/16 | 19.793 | 1.0000 | 1,147.375 | Unknown |
| Policy | 12/16 | 3.930 | 0.2500 | 242.333 | Unknown |
| Hybrid | 16/16 | 5.793 | 0.4375 | 353.250 | Unknown |

The policy's lower cost per success must be read alongside its four unserved
cases. A workload dominated by changed contracts would gain little from reuse
and still pay synthesis overhead. Subscription allocation, remote compute/energy,
and real production error costs were not measured.

## Artifacts and reproducibility

- [Machine-readable summary](results/2026-09-21-gpt55/summary.json)
- [Source, model, environment and evaluator identities](results/2026-09-21-gpt55/identity.json)
- [Frozen policy](results/2026-09-21-gpt55/frozen-policy.py) and [policy identity](results/2026-09-21-gpt55/frozen-policy-identity.json)
- [Training candidates and evaluations](results/2026-09-21-gpt55/training.json)
- [All 48 evaluation episodes](results/2026-09-21-gpt55/episodes.json)
- [Call/usage ledger](results/2026-09-21-gpt55/call-ledger.json), with request and observable response traces in `calls/`
- [Rejected setup accounting](results/2026-09-21-gpt55/prior-attempt.json)

The run used Python 3.13.1, Pi 0.84.2, and macOS 26.6.2 ARM64. Hardware CPU details
were unavailable under the local sandbox. The exact objective verifier, policy
executor, refinement loop, benchmark harness and lockfile hashes are recorded.
Human-readable traces omit model scratchpads and contain no authentication data.

The recorded lockfile hash describes the original run environment. The subsequent
CI security update to AnyIO 4.14.2 does not rewrite the archived identities or
measurements; a new run records its own dependency identity.

From `autocontext/`:

```bash
uv sync --group dev
uv run pytest benchmarks/policy_reuse/test_run.py
uv run python benchmarks/policy_reuse/run.py \
  --output benchmarks/policy_reuse/results/new-run \
  --prior-attempt benchmarks/policy_reuse/results/2026-09-21-pilot
```

The last command invokes the model again using existing Pi authentication and
carries the archived rejected setup into its accounting. Omit `--prior-attempt`
for an independent fresh run. Fresh model outputs and timings may differ.

Replay the frozen policy without any model calls:

```bash
uv run python - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, 'benchmarks/policy_reuse')
from run import PilotScenario
from autocontext.execution.policy_executor import PolicyExecutor
root = Path('benchmarks/policy_reuse/results/2026-09-21-gpt55')
protocol = json.loads((root / 'protocol.json').read_text())
results = PolicyExecutor(PilotScenario()).execute_batch(
    (root / 'frozen-policy.py').read_text(), seeds=protocol['heldout_seeds'])
print([r.score for r in results])
PY
```

Keep the evaluator-learning path first. Use this pilot as evidence for scoped
applicability/fallback work (AC-1020) and a later non-game transfer experiment
(AC-1029). Require broader, independently held-out quality/cost evidence before
production policy promotion (AC-1021). TypeScript parity remains deferred; this
change is an experiment with no production rollout.
