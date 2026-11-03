# Checkpoint-backed editing — milestone 8

This CPU-only example applies real tensor edits to a tiny safetensors checkpoint
inside the [adaptive editing workflow](../qwen_adaptive/README.md). W candidates
are typed, hashed edit programs with verified checkpoint outputs. They pass
through practice-based selection, restoration, native sealing, and separate
confirmation before a fixture audit. No Qwen model or GPU executes, and all
responses remain hand-authored fixtures rather than inference results.

From the repository's `autocontext/` directory:

```bash
uv sync --locked --group dev
uv run python ../examples/weight_evolution/qwen_checkpoints/run.py validate
uv run python ../examples/weight_evolution/qwen_checkpoints/run.py run-fixtures \
  --output ./qwen-checkpoint-run
# Exact resume re-verifies checkpoint bytes and saved evidence.
uv run python ../examples/weight_evolution/qwen_checkpoints/run.py run-fixtures \
  --output ./qwen-checkpoint-run
```

Choose a fresh path with no symlink ancestors. `validate` checks byte/hash locks,
the complete base tensor inventory, adapter/source pins, equal budgets, and
evaluation plans without materializing an edited checkpoint. Runtime code has no
live provider option, API request, Docker invocation, model download, or GPU path.

## Typed tensor changes

The fixture's base has four actual floating-point tensors: BF16 and F32 editable
weights, plus protected F16 and F64 tensors. Three W programs each replace two
scalar entries in the same immutable base. The selected program changes the
first BF16 scalar from 1 to 3 and the second F32 scalar from -1 to 2. Every other
payload byte remains unchanged. Container headers may be rewritten canonically;
unchanged tensor payloads are the invariant.

The edit language is `sparse_replace_bits`, with exact little-endian scalar hex
values. It supports BF16/F16/F32/F64 and has no executable Python, arithmetic
expression, pickle, dynamic import, or optimizer. Values must have the exact
dtype width and be finite; tensor names and flat indices must be unique and
sorted, and every replacement must change its original value.

Each `CheckpointContract` pins the complete input file and tensor inventory,
including names, shapes, dtypes, per-tensor hashes, and editable permissions.
Programs bind that contract and the expected input tensor hashes. The core
reuses M2's `TensorFile`, deterministic writer, and finite-value checks, then
independently reconstructs expected output bytes for every tensor. Verification
does not treat a stored success receipt as authority.

Bounds are deliberately small: at most 4 MiB per checkpoint, 64 tensors, 4,096
replacements per program, eight candidate specs, and 32 MiB of pinned candidate
outputs. This example further limits each program to two changed entries.
These are tiny CPU fixtures, not a production Qwen checkpoint format or budget.

## Integration with adaptive search

`TensorSessionAdapter` composes the existing provider adapter. A W proposal
selects a strict typed reference from the pinned program catalog. Its edit
operation materializes and verifies the checkpoint **after the durable dispatch
claim and before acknowledging the edit**. Practice probes independently verify
the referenced checkpoint again before returning their literal outputs. C and D
retain the exact base checkpoint. Edit/probe messages bind the input, program,
output, and verification hashes to that operation.

The frozen M5–M7 schemas and source pins remain unchanged. Their legacy strings
carry canonical checkpoint references as an explicitly labeled compatibility
projection. The authoritative export is the new `TensorCandidateArtifact` with
`representation=verified_safetensors_fixture`, verified checkpoint identities,
search lineage, native verification, and journal-derived costs. A free-form
description or a legacy comparison artifact alone is not native checkpoint
evidence.

All arms use M7's same three-round caps and practice-only feedback. Candidate
correctness follows a literal 1/3 baseline, 2/3 first candidate, and 3/3 second
candidate trajectory. C's third proposal fails; W's third candidate regresses;
D's third probe fails. All select round two. W restores the previously verified
second checkpoint, and D restores its decoding settings, before final practice
verification and sealing. The full workflow charges 33 operations: C uses 9,
W and D use 12 each. Failed attempts and restoration remain charged.

Byte counters account for adapter/provider exchanges; tensor storage has its
separate finite bounds above. Simulated seconds are not measured execution time
or a hard deadline. Tiny deterministic edits establish editing correctness, not
LLM-generated optimization or better model behavior.

## Native sealing, confirmation, and audit

Each native seal links the verified base/program/output hashes and tensor-change
receipt to the original practice selection, final restored revision, M6 export,
search completion, source identities, and retained costs. Before sealing or
confirmation, the bridge replays the search and re-verifies **every used
checkpoint**, including candidates that lost selection.

This extra verification matters on resume: existing M6 operations return saved
receipts without calling the adapter again. The native workflow therefore
checks materializations before relying on those receipts. Missing, changed, or
partial checkpoints fail instead of being regenerated. Once confirmation exists,
missing native selection evidence is not silently reconstructed.

All three searches seal before one confirmation look. The native wrapper verifies
the base and W checkpoint projections against those native seals, then calls the
existing confirmation gate with its separate three-question cohort. The default
fixture confirms all arms and completes a 60-cell audit. Its confirmation and
audit response catalogs are newly pinned to these checkpoint-reference cells;
prior M5/M7 observations are not relabeled as outputs from new tensors.

A separate veto fixture demonstrates that held-out rejection prevents the audit:

```bash
uv run python ../examples/weight_evolution/qwen_checkpoints/build_fixture.py \
  --output ./qwen-checkpoint-veto-fixture --confirmation-outcome veto
uv run python ../examples/weight_evolution/qwen_checkpoints/run.py \
  --fixtures ./qwen-checkpoint-veto-fixture run-fixtures --output ./qwen-checkpoint-veto
```

The builder performs tiny deterministic edits to freeze expected output hashes;
it does not generate answers from gold or run a model. It writes a fresh bundle
only. Changing evidence or candidate programs requires a distinct experiment;
confirmation feedback never returns to proposal generation or candidate choice.

## Evidence and remaining work

The bundle includes `base.safetensors`, `data.json`, `workflow.json`, independent
`confirmation.json`/`audit.json` catalogs, and byte/hash locks. Output includes
`materializations/<candidate-digest>/` with actual checkpoints and verification
receipts; `search/{C,W,D}/` journals; authoritative `native-{C,W,D}.json` seals;
the frozen selection; separate confirmation evidence; and the final summary.
`audit/` appears only when all arms confirm. Saved evidence contains portable
relative references rather than machine-specific absolute paths.

GPU acceptance, full Qwen reconstruction/inference, real benchmark reproduction,
inference-backed practice/confirmation, benchmark split selection and
contamination review, unconstrained tensor-program generation, optimizer
arithmetic, hard runtime limits, and repeated-search experiments remain open.
The trusted local fixture code is not an untrusted-program sandbox or remote
attestation. TypeScript parity is deferred. All scoring remains synthetic and
ineligible for real promotion or benchmark-performance claims.

See [M8 validation](MILESTONE8_VALIDATION.md) and the remaining
[GPU acceptance/reproduction gaps](../qwen_reproduction/MILESTONE4_VALIDATION.md).
