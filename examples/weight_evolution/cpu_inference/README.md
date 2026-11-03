# Tiny checkpoint inference on CPU — milestone 9

This example executes a small numerical model from verified safetensors bytes.
Its context, weight, combined, and decoding conditions produce their outputs
through actual CPU forward passes. There are no stored response catalogs.
The tasks and candidate edits are deliberately constructed to exercise the
evaluation workflow; their scores do not establish generalization or benchmark
improvements. Qwen inference, GPU execution, and live providers remain deferred.

From the repository's `autocontext/` directory:

```bash
uv sync --locked --group dev
uv run python ../examples/weight_evolution/cpu_inference/run.py validate
uv run python ../examples/weight_evolution/cpu_inference/run.py run \
  --output ./cpu-inference-run
# Resume the same pinned experiment from its retained evidence.
uv run python ../examples/weight_evolution/cpu_inference/run.py run \
  --output ./cpu-inference-run
```

Use a fresh output directory for a new experiment. Paths must not traverse
symlinks. A custom bundle is selected with `--bundle` before the subcommand:

```bash
uv run python ../examples/weight_evolution/cpu_inference/build_bundle.py \
  --output ./cpu-inference-bundle --confirmation-outcome pass
uv run python ../examples/weight_evolution/cpu_inference/run.py \
  --bundle ./cpu-inference-bundle validate
uv run python ../examples/weight_evolution/cpu_inference/run.py \
  --bundle ./cpu-inference-bundle run --output ./cpu-inference-custom-run
```

The builder writes a new bundle only. It constructs checkpoint bytes, bounded
edit programs, task inputs, and coordinator-side labels. It does not supply
predicted responses to the inference worker.

## Model and comparison conditions

The `token-sum-linear-v1` model has six input tokens and two output labels:
output ID 0 is `zero`, and output ID 1 is `one`.
It splits the prompt on whitespace and computes one logit per output:

```text
logits = bias + sum(weight[input_token] for input_token in prompt)
```

The checkpoint stores F32 weights and biases. The CPU implementation widens
these values to Python binary64 and accumulates token contributions in prompt
order. It emits exactly one output token. Greedy decoding selects the largest
logit, breaking ties by the lowest output ID. Sampling uses temperature-scaled
softmax over the ranked top-k outputs and a pinned SHA-256-derived draw.
The draw depends only on the seed, preserving common randomness across paired
conditions.

The base bias is `[0, 0]`, with these weight rows:

| Input token | Output 0 weight | Output 1 weight | Task label |
| --- | ---: | ---: | ---: |
| `anchor` | 8 | 0 | 0 |
| `gap` | 1 | 0 | 1 |
| `flipped` | 0 | 3 | 0 |
| `near` | 1 | 0 | 1 |
| `stochastic` | 0 | 1 | 0 |
| `hint` | 0 | 5 | Context prefix only |

Task labels belong to the coordinator's scorer. The worker request contains the
prompt, model contract, checkpoint identity, decoding settings, and resource
limits; it contains no gold label or grading rule.

| Condition | Checkpoint | Context | Decoding |
| --- | --- | --- | --- |
| A: baseline | Base | Empty | Greedy |
| C: context | Base | Prefix `hint` | Greedy |
| W: weights | Predeclared beneficial checkpoint | Empty | Greedy |
| CW: combined | Predeclared beneficial checkpoint | Prefix `hint` | Greedy |
| D: decoding | Base | Empty | Sample, temperature 1, top-k 2, seed 406 |

The beneficial W program replaces the `flipped` row with `[8, 0]`. The harmful
program replaces the `anchor` row with `[0, 8]`. Each changes two scalar entries
and derives independently from the same immutable base. The existing bounded
tensor-edit primitives verify the complete inventory, permitted tensor scope,
finite values, exact changes, and unchanged payloads.

The study fixes its W candidate, context, and decoding settings before any
inference. Practice evaluates these choices; it does not select candidates or
feed model results back into search. A separate acceptance sequence evaluates
the base, beneficial, harmful, and restored base checkpoints on practice inputs.
For `flipped`, the base and restored base produce `[0, 3]`, while the beneficial
checkpoint produces `[8, 0]`. The harmful edit instead makes `anchor` incorrect.
The coordinator checks improvement, degradation, and restoration of the base
logits and outputs. These are hand-designed programs, not edits proposed by an
LLM or learned by an optimizer.

## Constructed splits and confirmation veto

Each split contains five cases, one per task token. Practice uses the token
once, confirmation repeats it twice, and the final audit repeats it three times.
The inputs and role identities differ, but the underlying task families and
labels are deliberately correlated. These splits exercise separation of
practice evaluation, confirmation, and audit; they do not estimate
out-of-distribution performance or establish that a learned change generalizes.

For the default bundle, the analytically expected correct-case counts are:

| Split | A | C | W | CW | D |
| --- | ---: | ---: | ---: | ---: | ---: |
| Practice | 1/5 | 3/5 | 2/5 | 4/5 | 5/5 |
| Confirmation | 1/5 | 3/5 | 2/5 | 4/5 | 4/5 |
| Final audit | 1/5 | 3/5 | 2/5 | 4/5 | 4/5 |

The runner computes outputs from checkpoint bytes; the scorer checks them
against the task labels afterward. The table describes the controlled example,
not a lookup table used to generate outputs. Context corrects `gap` and `near`;
the W edit corrects `flipped`; decoding can also correct `stochastic`.

The study freezes its candidate choices before the first dispatch, including
practice. The confirmation gate requires a gain of at least 0.1 over the matched
baseline. A veto prevents the final audit and cannot trigger another candidate
choice in the same experiment.

```bash
uv run python ../examples/weight_evolution/cpu_inference/build_bundle.py \
  --output ./cpu-inference-veto-bundle --confirmation-outcome veto
uv run python ../examples/weight_evolution/cpu_inference/run.py \
  --bundle ./cpu-inference-veto-bundle run --output ./cpu-inference-veto-run
```

The veto bundle changes the confirmation inputs for `gap`, `near`, and
`stochastic` to four repetitions. It leaves their labels unchanged. Context and
the selected W edit still help, but D returns to the baseline score of 1/5 and
fails the gain threshold. This rejection follows from newly computed logits
and sampling probabilities, without injecting failed responses.

## Execution evidence and resource limits

The trusted worker runs in a separate local Python process. Requests bind the
checkpoint size and hash, input tokens, decoding settings, and finite resource
limits. Results retain logits and the emitted token; process receipts bind
runtime/source identities and measured execution telemetry. New CPU inference
records truthfully declare `model_execution_performed=true`, `device=cpu`, and
no GPU execution. Promotion and real benchmark claims remain ineligible.

Durable claims precede dispatch. Resume verifies retained requests, results,
checkpoint bytes, and bindings. An unresolved claim cannot be silently retried;
missing completed evidence cannot be replaced by another inference call.
Runtime-dependent telemetry is evidence of that execution, so independent
runs need not have byte-identical receipts even when their model outputs match.

The model supports at most 32 vocabulary tokens, 2–8 output labels, 64 input
tokens, an 8 KiB prompt, and a 4 MiB checkpoint. One worker batch contains at
most 32 requests and produces one token per request. Bounded request and output
streams prevent unbounded transport buffers. Worker settings allow 256–2,048
MiB and a wall deadline of 0.1–30 seconds; the defaults are 512 MiB and 5 seconds.
The example further caps each input at 16 tokens and the entire study at 100
forward passes, 512 input tokens, and 100 output tokens. The default plan uses
95 forward passes; a confirmation veto withholds the final 25.

The parent enforces the wall deadline and terminates overdue workers. Linux
also applies an address-space limit with `RLIMIT_AS` and reads the worker's
post-exec peak RSS from `/proc/self/status` (`VmHWM`). Linux `getrusage` can
retain the launcher's earlier memory peak, so it is not used for this measurement.
Missing or malformed peak evidence fails closed. macOS uses monitored RSS
together with the structural caps. RSS polling can overshoot between samples
and is not a hard address-space limit. CPU-time limits also apply. The recorded
resource policy distinguishes these platforms. This is containment for trusted,
fixed model code, not a sandbox for arbitrary programs or remote attestation.

## Compatibility and remaining work

M5–M8 fixture contracts retain their original `model_execution_performed=false`
meaning. M9 adds separate inference and workflow evidence rather than relabeling
literal observations or weakening those frozen schemas. It reuses checkpoint
verification and durable publication primitives where their semantics fit.

The example does not perform adaptive selection, execute Qwen, reconstruct its
full checkpoint, reproduce published benchmarks, call live providers, generate
arbitrary edit programs, or run GPU acceptance. Learned search, independent benchmark splits,
contamination review, production model resources, and TypeScript parity remain
future work. Its purpose is to verify that actual checkpoint bytes can cause
outputs that flow through controlled comparison and a separate confirmation gate.

See [M9 validation](MILESTONE9_VALIDATION.md) for measured results, replay checks,
artifact hashes, and the remaining platform and reproduction gaps.
