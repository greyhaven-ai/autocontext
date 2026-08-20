# Kernel evolution MVP

This runnable example exercises the same control/data-plane split intended for
one pinned KernelBench problem. AutoContext generates candidates, makes
promotion decisions, accumulates feedback, and persists lineage. A separate
host-owned command compiles/checks/times candidate and incumbent artifacts and
writes `autocontext.kernelbench-eval/v2` JSON.

Run it from the Python package directory:

```bash
cd autocontext
uv run --frozen python ../examples/kernel_evolution/run.py
```

The synthetic adapter requires no GPU and demonstrates orchestration only. It
deliberately produces a wrong-but-fast candidate, a correct improvement below
the 5% threshold, and a winner. The provisional winner then faces a second
host-owned problem profile whose seed/order commitment differs while every
correctness, tolerance, trial-count, warmup, and timing field remains compatible.
Inspect the printed run directory for the manifest, exact content-addressed
sources, primary and confirmation reports, append-only lineage, and champion.

For a pinned live comparison on an NVIDIA H100, use the
[KernelBench L1/P1 H100 example](kernelbench_h100/README.md). It runs the real
external contract against an AutoKernel starter and a tuned Triton candidate.

For a real run, replace `fake_kernelbench_adapter.py` with an operator-owned
GPU adapter. Keep `problem.json`, reference inputs, tolerances, hidden seeds,
warmups, timing schedule, and toolchain outside the generated candidate's
control. The adapter must accept the candidate, current incumbent, and output
report paths and write the strict JSON contract; stdout remains diagnostic.
See [the kernel evolution guide](../../autocontext/docs/kernel-evolution.md).
