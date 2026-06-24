# Draft: autocontext 0.5.0 → 0.10.0 X/Twitter update

Linear: AC-836

Status: first draft, needs edit pass before posting

## Working hook options

1. We last posted a serious autocontext update around `0.5.0`. We just shipped `0.10.0`. In between, it quietly turned from an eval loop into a recursive agent harness with contracts, memory, training, and deployment-aware model selection.
2. Since `0.5.0`, autocontext has become less of a benchmark runner and more of an operating system for agent improvement.
3. The short version: autocontext now does the loop, records the evidence, curates the lessons, trains the local model, and gates whether the result is safe to reuse.

## Long-form X article draft

We last did a real public autocontext update around `0.5.0`. Today `0.10.0` is out.

The project has changed a lot in that gap.

At `0.5.0`, autocontext was already useful as an iterative agent harness: run scenarios, score outputs, keep the better strategy, and build a trace of what happened.

Since then, the work has been about making that loop durable enough to compound.

### 1. The CLI became a contract, not vibes

`0.6.0` was mostly about making the surface reliable.

We added a shared canonical CLI contract, closed Python/TypeScript parity gaps, and added contract probes that can check directories, terminals, services, artifacts, cleanup, media, and distributed execution behavior.

That sounds dry, but it matters: if agents are going to use a toolchain, the toolchain needs a stable shape. Commands cannot randomly mean different things across runtimes. Observations cannot silently pass when data is missing.

This release made autocontext easier for agents to operate without special-case prompt lore.

### 2. The loop learned how to train itself

`0.7.0` was the big local-training release.

The harness can now export training data, train local models, register the resulting model, and route future runs through that trained artifact. The MLX path handles fast local iteration; the TRL path gives us cross-platform OPD/GKD and GRPO/RLVR on non-Apple hardware.

We added:

- on-policy distillation
- GRPO/RLVR reward training from scenario verifiers
- an R1-style distill-then-RLVR recipe
- score-conditioned training
- reward-weighted loss
- held-out validation and checkpoint selection
- self-improvement loops that generate, score, filter, and retrain on their own samples

The point is not just “train a model.”

The point is to let the harness produce experience, turn that experience into a dataset, distill it into a cheaper runtime, and then use that runtime in the next loop.

That is the recursive part.

### 3. Runs became inspectable systems

`0.8.0` focused on evidence, reports, and knowledge lifecycle.

We added run progress reports, utilization reports, campaign reports, goal-run reports, negative result ledgers, span-level credit attribution, and exploration-collapse guards.

We also made lessons and playbooks more serious.

Instead of dumping everything into memory forever, autocontext can stage playbook changes for approval, curate derived lessons from live markdown, retire stale lessons, and keep negative evidence around so the system can remember what not to repeat.

This is important because self-improvement systems do not only fail by forgetting useful things.

They also fail by preserving junk.

### 4. Exploration became more controlled

`0.9.0` added several default-off exploration controls.

Panel/fusion roles let multiple role-specific agents fan out and synthesize. Annealing gates can accept small regressions early and tighten over time. Lévy scout prompt mutation adds heavy-tailed exploration steps when the loop gets stuck.

We also added typed rubric specs and human-anchor rubric patch proposals, plus runtime parity fixes for role routing, strategy-package imports, and Hermes advisor checkpoints.

The theme was simple: explore more, but make the exploration observable, reproducible, and optional.

### 5. Training scaled past laptop-sized models

`0.10.0` is the larger-model planning release.

The training pipeline now has opt-in CUDA/TRL scale profiles for:

- 7B QLoRA RLVR
- sharded 32B student / 72B teacher distillation
- multi-device sharding strategies
- per-device and global memory budgets
- base-model parameter and quantization metadata
- deployment-target VRAM gating in the model registry

This does not replace the fast MLX path. It gives the project a path from “iterate locally” to “train seriously on NVIDIA/cloud hardware” without changing the scenario/verifier contract.

The same harness should be able to run a small local experiment, collect traces, distill behavior, and then plan a larger training job when the bottleneck is model capacity.

### What changed overall

Since `0.5.0`, autocontext has moved in five directions:

1. stronger contracts
2. deeper traces
3. curated long-term knowledge
4. recursive training loops
5. larger deployment-aware model plans

The goal is still the same: agents should get better from their own validated experience.

But the system around that goal is much more real now.

It can run tasks, inspect what happened, preserve the useful parts, reject the bad parts, train smaller models from the evidence, and choose artifacts that fit the deployment target.

If you tried autocontext around `0.5.0`, `0.10.0` is a very different project.

Install:

```bash
uv tool install autocontext==0.10.0
bun add -g autoctx@0.10.0
```

Release notes: <https://github.com/greyhaven-ai/autocontext/blob/main/CHANGELOG.md#0100---2026-06-24>

Repo: <https://github.com/greyhaven-ai/autocontext>

## Thread draft

1/ We last did a serious autocontext update around `0.5.0`.

`0.10.0` is out now, and the project has changed a lot.

It went from an eval loop into a recursive agent harness with contracts, traces, memory, training, and deployment-aware model selection.

2/ `0.6.0` made the CLI a contract.

Python + TypeScript parity, canonical command surfaces, and contract probes for terminals, directories, services, artifacts, cleanup, media, and distributed execution.

Agents need tools with stable shapes, not vibes.

3/ `0.7.0` was the local-training jump.

Autocontext can export training data, train local models, register them, and route future runs through the trained artifact.

MLX for fast local iteration. TRL for cross-platform OPD/GKD and GRPO/RLVR.

4/ That release also added the recursive pieces:

- on-policy distillation
- GRPO/RLVR from scenario verifiers
- R1-style distill → RLVR
- score-conditioned training
- reward-weighted loss
- self-improvement loops

The harness can turn validated experience into a model.

5/ `0.8.0` made runs easier to inspect.

Progress reports, utilization reports, campaign reports, goal-run reports, negative-result ledgers, span credit attribution, and exploration-collapse guards.

The system now records more than “pass/fail.” It records why.

6/ We also made memory less naive.

Playbook updates can be staged for approval. Lessons are derived from live markdown. Stale lessons can be retired. Negative evidence stays available so the loop can avoid repeating failed paths.

Self-improvement needs curation, not just accumulation.

7/ `0.9.0` added default-off exploration controls.

Panel/fusion roles, annealing gates, and Lévy scout prompt mutation.

The goal: explore more aggressively when stuck, while keeping the behavior observable, reproducible, and opt-in.

8/ `0.9.0` also tightened runtime parity:

- typed rubric specs
- human-anchor rubric patch proposals
- role routing fixes
- strategy-package import side-effect contracts
- Hermes advisor checkpoint provenance

Less accidental drift between runtimes.

9/ `0.10.0` adds larger-model training plans.

Opt-in CUDA/TRL profiles for 7B QLoRA RLVR and sharded 32B/72B distillation, plus multi-device sharding, memory budgets, quantization metadata, and deployment VRAM gating.

Local iteration → serious training path.

10/ The through-line since `0.5.0`:

Autocontext now runs the loop, records the evidence, curates the lessons, trains from the traces, and gates whether the result fits the deployment target.

That is the recursive agent-improvement loop we wanted.

11/ Try `0.10.0`:

```bash
uv tool install autocontext==0.10.0
bun add -g autoctx@0.10.0
```

Repo: <https://github.com/greyhaven-ai/autocontext>
Release notes: <https://github.com/greyhaven-ai/autocontext/blob/main/CHANGELOG.md#0100---2026-06-24>

## Edit notes

- Add screenshots or diagrams if posting as a long-form X article.
- Decide whether to lead with “recursive agent harness” or “training from validated traces.”
- Consider a shorter version for people who do not know MLX/TRL/GRPO terminology.
