# Case study: on-policy distillation beats RLVR at matched compute

This is a validation result produced end to end on autocontext's own training stack: its
cross-platform `trl` backend, driven by autocontext's scenario plus programmatic-verifier
abstraction. It reproduces a frontier post-training result (on-policy distillation is far
more sample-efficient than reinforcement learning from verifiable rewards) on a real
benchmark, and it shows the training stack is more than plumbing.

## Result

Distilling a 1.5B student from a 3B teacher on GSM8K (grade-school math), at matched
compute, scored greedily on 100 held-out test problems:

| Method                                             | Accuracy            | Delta vs baseline | Teacher gap closed |
| -------------------------------------------------- | ------------------- | ----------------- | ------------------ |
| student baseline (Qwen2.5-1.5B-Instruct)           | 0.64                |                   |                    |
| teacher ceiling (Qwen2.5-3B-Instruct)              | 0.85                |                   |                    |
| **GKD @ 1000 steps** (3 seeds: 0.68 / 0.73 / 0.70) | **0.703 +/- 0.021** | **+0.063**        | **30%**            |
| **GKD @ 2000 steps**                               | **0.73**            | **+0.09**         | **43%**            |
| GRPO @ 1000 steps                                  | 0.64                | 0.00              | 0%                 |

Three things hold:

1. The lift is real, not noise. Across 3 seeds, on-policy distillation (GKD) lands at
   0.70 +/- 0.02 with every run above baseline; the seed-to-seed std (0.02) is far below the
   +6.3 point delta.
2. It scales with compute. Doubling to 2000 steps closes 43% of the gap, up from 30%.
3. RLVR (GRPO) is flat at matched compute. The dense per-token distillation signal moved the
   student where the sparse end-of-episode reward did not, at the same step budget.

## What this exercises in autocontext

- The cross-platform `trl` backend (`autoctx train --backend trl`), which wraps HuggingFace
  TRL's `GKDTrainer` (`--trl-mode gkd`, on-policy distillation) and `GRPOTrainer`
  (`--trl-mode grpo`, RLVR). The MLX backends (`opd`, `grpo`) are the Apple Silicon
  counterparts; `trl` is the path for Linux, NVIDIA, and larger runs.
- The scenario plus verifier abstraction. The same programmatic reward (a GSM8K
  exact-integer check exposed through autocontext's agent-task interface) serves both the
  GKD and GRPO arms, so the comparison is apples to apples.
- The methodology that makes the number trustworthy: a baseline probe with a
  teacher-beats-student headroom gate, matched compute across arms, and a held-out
  before/after measurement.

## Why the number is trustworthy: the diagnostic arc

The positive was not cherry-picked. The same harness's gates were correct at every step:

1. A combinatorial task (divisibility antichain) showed near-zero headroom: the 3B teacher
   was no better than the 1.5B student (a strategy ceiling that capability does not move).
   The gate flagged it as a task that cannot measure distillation. Correct null: wrong task.
2. GSM8K at 100 steps had real headroom (0.21) but both arms were flat. The binding
   constraint was compute, not the method. Correct null: too few steps.
3. GSM8K at 1000 and 2000 steps produced the signal, once headroom and compute were both
   present.

Each null was diagnostic, which is why the lift at scale is credible.

## Honest caveats

- Error bars come from 3 seeds on a 100-problem test set (roughly +/- 5% binomial CI per
  point). The +6.3 mean is well above the seed std, but this is one model pair on one
  benchmark, not a broad sweep across sizes and tasks.
- GRPO being flat means "far less sample-efficient at 1000 to 2000 steps," which is the
  point of distillation's edge. GRPO on GSM8K typically needs more steps and tuning; it was
  not tuned to convergence here.
- The 2000-step point is a single seed. The direction (30% to 43% of the gap closed) is
  clear; the exact slope is not pinned.

## Reproduce

The matched-compute experiment (1000 / 2000 steps, 384 training prompts, seeded repeats,
held-out GSM8K assessment) was driven by a companion harness that calls the `trl` backend's
public training function, `run_trl_training`, which exposes the step / prompt / seed knobs
the experiment needs. On a GPU host with `trl`, `torch`, and `peft` installed:

```python
from pathlib import Path

from autocontext.training.autoresearch.trl_backend import run_trl_training

# on-policy distillation arm (teacher distilled into the student via dense per-token reverse KL)
gkd = run_trl_training(
    mode="gkd",  # "grpo" for the matched RLVR baseline (same scenario verifier as the reward)
    scenario_name="<your-scenario>",  # any registered agent task with a programmatic verifier
    output_dir=Path("out/gkd"),
    student_model="Qwen/Qwen2.5-1.5B-Instruct",
    teacher_model="Qwen/Qwen2.5-3B-Instruct",
    n_prompts=384,
    max_steps=1000,  # 2000 for the trajectory point
    seed=0,  # vary (0 / 1 / 2) for error bars
)
```

The companion harness wraps this with the baseline probe (student and teacher), the
teacher-beats-student headroom gate, and greedy held-out scoring of the saved adapter. The
exact GSM8K scenario, the Modal GPU harness, and the raw result JSON live in the companion
research repository, so the domain-specific experiment code stays out of autocontext core.

`<your-scenario>` is any autocontext agent task with a programmatic verifier (see
[mlx-training.md](mlx-training.md) for backend details and
[the scenario docs](https://autocontext.ai/docs/concepts) for the interface). Teacher and
student must share a tokenizer / logit vocabulary (the backend rejects a mismatch up front);
within Qwen2.5, the 0.5B / 1.5B / 3B models share vocab while 7B and larger do not.

> The convenience CLI `autoctx train --backend trl --trl-mode gkd|grpo` invokes the same
> backend, but it uses the default (short) training length and does not expose a step count,
> so it is an entry point / smoke check, not the matched-compute run above.

## Takeaway

autocontext's training stack reproduces, end to end and cross-platform, the central result of
on-policy distillation, on its own scenario and verifier abstraction, with error bars and a
compute trajectory. The dense per-token signal of distillation is markedly more
sample-efficient than sparse verifiable-reward RL at matched compute.
