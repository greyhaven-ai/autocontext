# Case study: the recursive loop, closed end to end on local MLX

autocontext's premise is a loop: an agent attempts a task, the verifier scores the attempts,
the best trajectories train a model, and the _next_ run uses that trained model — with no human
in the middle. This is that loop running end to end on a single Mac: train a small LoRA adapter
on a scenario's verifier-scored strategies, publish and auto-activate it in the model registry,
and have the agent provider auto-resolve and serve it on the next run. The served model proposes
**41.9% better** strategies than the untrained base, and nothing about which model to serve is
hardcoded — it is resolved from the registry the training run wrote to.

## Result

`grid_ctf` scenario, base model `mlx-community/Qwen2.5-0.5B-Instruct-4bit`, 8 strategies
sampled per measurement and scored by the scenario's own verifier:

| Stage                                  | Mean verifier score  | Valid JSON rate |
| -------------------------------------- | -------------------- | --------------- |
| **run N** — base model as the agent    | 0.5809               | 75%             |
| **run N+1** — auto-served LoRA adapter | **0.8241**           | 100%            |
| delta                                  | **+0.2432 (+41.9%)** |                 |

The adapter was fine-tuned for 80 LoRA steps on the 60 highest-scoring strategies the loop
accumulated (mean verifier score 0.849). The whole loop — train, publish, auto-resolve, serve,
re-measure — ran in **43 seconds**. The in-training assessment (0.8565) independently agreed
with the served-adapter measurement (0.8241), so the metric the training run reports is the
score the served model actually delivers.

## What "closed loop" means here

The point is not that fine-tuning improves a model — that is expected. The point is that the
next run picks up the trained model **on its own**:

```
run N      base Qwen2.5-0.5B-Instruct proposes grid_ctf strategies        -> 0.58
train      LoRA SFT on the elite verifier-scored strategies               (38s)
publish    register + activate the adapter; record base_model on it       -> state=active
bridge     scenario_bound resolver -> plan_local_client -> MLXLMClient     -> auto-selected
run N+1    AUTOCONTEXT_AGENT_PROVIDER=mlx serves base + adapter            -> 0.82
```

The `bridge` step is the load-bearing one. The serving run is given no model path. It calls
`_resolve_local_record(settings, scenario)`, which finds the active record the training run
published, and `plan_local_client(record)`, which routes an `mlxlm`/`opd` adapter to
`MLXLMClient(base=record.metadata["base_model"], adapter_path=record.checkpoint_path, ...)`.
That is why the registry record has to carry the base model the adapter was trained against —
an adapter checkpoint is useless without it — and why the publish step records it.

## Does it compound? Multi-generation self-improvement

The single step above trains on a curated near-optimal elite (strategies sampled across the
space and scored by the verifier), so it shows the ceiling the loop can reach with good data.
The stronger claim is that the loop improves on its OWN output with no external data: each
generation, the currently-served model proposes, the verifier scores, the best of everything
proposed so far becomes the next adapter's training set, and the next generation is served by
that adapter. Three generations bootstrapping from the base model's cold-start proposals:

| Generation     | Mean   | Best   | Valid JSON |
| -------------- | ------ | ------ | ---------- |
| gen 0 (base)   | 0.5767 | 0.6885 | 13/20      |
| gen 1 (served) | 0.5952 | 0.7254 | 20/20      |
| gen 2 (served) | 0.5998 | 0.7351 | 20/20      |
| gen 3 (served) | 0.6194 | 0.7369 | 20/20      |

Mean and best both rise monotonically, valid-JSON rate goes 13 -> 20/20, and the whole 3-gen
run takes 33s. The gain (+7.4%) is far smaller than the single-step +41.9%, and honestly so:
bootstrapping from the base model's own weak, low-diversity cold-start distribution is slower
than training on a globally-curated elite. The point is not the magnitude but the shape — the
loop compounds on its own verifier-scored output, generation over generation, with no human and
no external data. The only external signal is the verifier, which scores but never generates.

## Reproduce

Requires Apple Silicon with the mlx extra plus mlx-lm (`uv pip install mlx mlx-lm`). The base
model downloads once from the `mlx-community` Hugging Face repo.

```bash
uv run python scripts/demo_recursive_loop.py            # single step (train -> serve -> +41.9%)
uv run python scripts/demo_recursive_loop_multigen.py    # multi-generation self-improvement
```

The script is self-contained: it builds the elite training set from the scenario's verifier,
calls `run_mlxlm_training`, publishes via `publish_training_output(..., auto_activate=True)`,
then resolves and serves the adapter through the exact code path the agent provider uses
(`scenario_bound_clients`), and prints the before/after verifier scores.

## Two fixes this surfaced

Running the loop on a game scenario exposed two real gaps, both fixed alongside this demo:

1. **Game scenarios produced an empty task prompt.** `ScenarioInterface` scenarios expose
   `describe_rules` / `describe_strategy_interface` / `describe_evaluation_criteria` but no
   `get_task_prompt` or `description`, so `resolve_scenario_context` returned `""` — every game
   scenario was untrainable on the adapter backends. It now composes the `describe_*` methods
   into a task instruction.
2. **The in-training assessment fed the model a raw prompt.** `_assess_mlxlm` passed the bare
   task string to `generate()`, but mlx-lm's LoRA trainer and the serving path both apply the
   instruct chat template. An instruct model given a raw prompt emits prose, not scorable JSON,
   so the in-training metric read ~0 even when the adapter was good. Assessment now applies the
   chat template (`format_assess_prompt`), matching training and serving.
