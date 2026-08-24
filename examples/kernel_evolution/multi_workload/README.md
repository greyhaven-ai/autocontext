# Multi-workload kernel evolution study

This example runs variable-shape matrix multiplication, fused
elementwise/reduction, and causal-attention families through the same
`KernelEvolutionRunner`, external evaluator, confirmation, promotion, lineage,
and generation-budget contracts. It then re-evaluates promoted champions on
the other workload families and a second synthetic hardware identity.

Run it from the Python package directory:

```bash
cd autocontext
uv run --frozen python ../examples/kernel_evolution/multi_workload/run.py
```

The output contains the immutable manifest and materialized primary,
confirmation, and hardware-transfer contracts; each workload's ordinary
kernel run directory; and `study_report.json`. The study report deliberately
has no aggregate scalar score. It retains each workload's primary and fresh
confirmation correctness slices, per-case floors, protocol/reference/hardware
identities, bounded usage, promotion or plateau, transfer failures, and
specialist/generalizing classification. An artifact can appear in
`portable_champion_artifact_digests` only after it passes every target workload
and the required shape, hardware, and workload-family transfer dimensions.

The checked-in adapter is deterministic synthetic orchestration evidence. Its
latencies are not measurements and must never be cited as accelerator results.
For a production study, replace it with an operator-owned evaluator while
preserving the same report and study models. Pin each trusted reference and
runtime image, keep exact primary/confirmation cases private, reserve a fresh
confirmation plan for each adaptive look, use an OS-isolated protected
evaluator boundary, and publish the resolved accelerator identity and raw
receipts with the study.

The checked-in runbook caps every family at two proposals, 10,000 tokens, $1,
and 300 seconds. Those deliberately small values keep CI deterministic; real
budgets belong in an immutable operator manifest and remain independently
enforced by `KernelGenerationBudget`.
