# Adaptive editing with actual CPU inference

This example searches a finite set of context, tensor-edit, and decoding
choices using measured practice results. Each probe executes the trusted tiny
F32 token-sum classifier over verified safetensors bytes. GPU execution remains
deferred. The example uses no model downloads, live providers, or stored response
catalogs.

The inputs are deliberately constructed and correlated across splits. Results
demonstrate adaptive execution and evidence handling; they do not establish
language-model quality, generalization, or published benchmark reproduction.

## Run

From the Python package directory (`autocontext/`):

```bash
uv sync --locked --group dev
uv run python ../examples/weight_evolution/cpu_adaptive/run.py validate
uv run python ../examples/weight_evolution/cpu_adaptive/run.py run \
  --output ../runs/cpu-adaptive
```

Validation verifies the input pins, contract, checkpoint, and worst-case budget
without materialization or inference. The run command uses bounded local CPU
subprocesses. Repeating it with the same output directory verifies and resumes
retained evidence; a completed study performs no additional inference.

To rebuild the input bundle in a fresh directory:

```bash
uv run python ../examples/weight_evolution/cpu_adaptive/build_bundle.py \
  --output ../runs/cpu-adaptive-bundle
```

The builder writes a base checkpoint, `study.json`, and `bundle.lock.json`.
It computes the expected checkpoint hashes by applying each permitted edit in
a temporary directory. It performs no forward passes and stores no responses.
Every candidate's output remains subject to independent verification at run time.

## Search contract

The study pins five checkpoints (one base and four sparse edits), four options
per editing mechanism, three proposal rounds per mechanism, a complete practice
cohort, a separate confirmation cohort, and a final audit cohort. A candidate
can only change the surface permitted for its mechanism:

| Condition | Editable surface | Selection |
| --- | --- | --- |
| A | None | Fixed base checkpoint, empty context, greedy decoding |
| C | Context prefix | Best complete practice score, earliest candidate on ties |
| W | Pinned sparse tensor program | Best complete practice score, earliest candidate on ties |
| CW | Selected C prefix and W checkpoint | Predetermined composition, no additional search |
| D | Sampling temperature and top-k | Best complete practice score, earliest candidate on ties |

All C/W/D searches receive the same number of proposals and the same practice
cases. Each arm probes the baseline, runs three proposed trials, and rechecks
the selected candidate after restoration. Baseline participates in selection.
Different mechanisms can consume different numbers of input tokens; the report
retains actual costs under the same predeclared caps.

The deterministic policy sees option metadata and measured practice counts.
An improving trial continues to the next step in that direction. A trial that
does not improve switches to an unused direction, then uses the pinned order
when no new direction remains. Option IDs describe edits, not expected scores.
No confirmation or audit content enters proposal or selection decisions.

The evaluation seed is pinned to 406 outside editor control. Decoding candidates
may change temperature and top-k, but cannot select a seed. Pairing keeps the
same seed across candidate evaluations.

The default example includes a later regressing context choice, a later harmful
weight edit, and a later regressing decoding choice. The earlier winner must
survive each regression. Restoration re-verifies checkpoint bytes and reproduces
the selected candidate's logits and emitted tokens.

## Confirmation and veto

The chosen candidates are sealed before confirmation. The confirmation gate
makes one fixed look at C, W, and D against A. It requires each to exceed the
predeclared minimum gain. A veto withholds audit and does not restart search.
CW is reported as the fixed composition rather than receiving extra selection.

The separate veto bundle changes only the declared confirmation cohort design:

```bash
uv run python ../examples/weight_evolution/cpu_adaptive/build_bundle.py \
  --output ../runs/cpu-adaptive-veto-bundle \
  --confirmation-outcome veto
uv run python ../examples/weight_evolution/cpu_adaptive/run.py \
  --bundle ../runs/cpu-adaptive-veto-bundle run \
  --output ../runs/cpu-adaptive-veto
```

The veto follows from actual CPU outputs. It does not inject failed responses
or override the gate's outcome.

## Budgets, provenance, and failure handling

The default bundle permits nine proposals, five checkpoint materializations,
130 forward passes and output tokens, and 1,024 input tokens. Each batch has
a five-second parent wall deadline and a 512 MiB memory policy. Linux installs
hard address-space and CPU limits; macOS uses monitored RSS with recorded
sampling overshoot. The model and transport also have structural size caps.

Durable claims reserve work before dispatch. Failed or ambiguous work remains
charged. Resume checks ordered claims, saved receipts, checkpoint seals,
runtime and source identities, selection, and gate evidence. It refuses to
recreate missing completed evidence or silently repeat an incomplete dispatch.

CPU inference receipts contain real measured timing and memory evidence.
Semantic digests exclude timing variability; full receipt digests retain it.
`complete.json` summarizes the measured score histories, proposal decisions,
selected options, restored behavior, and actual per-arm search usage against
the common capacity. Each arm's usage includes its baseline, three proposals
and trial probes, and restoration. Shared checkpoint preparation and subsequent
comparison/confirmation/audit costs remain visible in the total study usage.

The output directory retains these records:

| Path | Evidence |
| --- | --- |
| `manifest.json` | Pinned study and runtime identities |
| `claims/` and `results/` | Ordered reservations, operation inputs, CPU receipts, and outcomes |
| `materializations/` and `seals/` | Verified checkpoint bytes and native identities |
| `selection.json` | Practice-selected candidates frozen before confirmation |
| `reports/practice.json` | A/C/W/CW/D practice scores and descriptive contrasts |
| `reports/confirmation.json` and `confirmation.json` | Fixed confirmation scores and one-look gate |
| `reports/audit.json` | Final comparison, present only after confirmation passes |
| `complete.json` | Search decisions, costs, outcome, and evidence digests |

The coordinator reuses the tensor registry, checkpoint verification, CPU runner,
and durable publication helpers. Its new CPU contracts preserve the meaning of
the earlier fixture-only adaptive schemas.

This is a resource boundary around trusted fixed code, not a sandbox for
arbitrary programs or remote attestation. Qwen inference, GPU acceptance,
open-ended generated programs, live model proposals, independent benchmark
studies, and TypeScript execution parity remain deferred.
