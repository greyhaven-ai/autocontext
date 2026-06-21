# Local Training

How autocontext-exported datasets feed local MLX or CUDA training. Use
this when the user asks "can I train a model from my Hermes data" or
when an agent needs to scope training expectations.

> **Command availability.** `autoctx hermes export-dataset`,
> `autoctx hermes train-advisor`, and `autoctx hermes recommend` ship
> together in the Hermes-integration path. Run `autoctx hermes --help`
> to confirm what is installed locally before recommending the flow
> below. `autoctx train` is for Autocontext scenario datasets, not
> Hermes curator-advisor checkpoints.

## Scope (read this first)

`autoctx hermes train-advisor` produces **narrow advisor classifiers**,
not full agent replacements. The expected use is: should this Curator
decision have been made? Should this skill be archived, consolidated,
pruned, or left alone?

**Small personal Hermes homes will not produce frontier-quality
models.** The size and diversity of the dataset matter more than the
training pipeline. If the user has < 100 curator runs, propose a
shadow-evaluation loop instead of training.

## End-to-end flow

1. Export a labeled dataset:

   ```bash
   autoctx hermes export-dataset \
       --kind curator-decisions \
       --home ~/.hermes \
       --output training/hermes-curator-decisions.jsonl
   ```

2. Inspect the dataset shape:

   ```bash
   head -1 training/hermes-curator-decisions.jsonl | jq .
   ```

   Each row is a flat feature vector + label + confidence. See the
   AC-705 module docstring for the canonical schema.

3. Train an advisor model:

   ```bash
   autoctx hermes train-advisor \
       --data training/hermes-curator-decisions.jsonl \
       --logistic \
       --checkpoint training/hermes-advisor.json \
       --json
   ```

   Use `--baseline` first for a floor. Use `--mlx` on Apple Silicon or
   `--cuda` on PyTorch/CUDA hosts when the optional extra is installed.

4. Surface advisor predictions back to Hermes Curator as **read-only**
   recommendations. Curator stays the mutation owner.

   ```bash
   autoctx hermes recommend \
       --home ~/.hermes \
       --advisor training/hermes-advisor.json \
       --output training/hermes-recommendations.jsonl \
       --json
   ```

## Backend selection

- **baseline**: majority-class floor; no checkpoint needed.
- **logistic**: pure-Python fallback; works in plain CI and small homes.
- **MLX**: Apple Silicon laptops with plenty of RAM. Quick iteration.
- **CUDA**: NVIDIA hosts with PyTorch; falls back to CPU torch when CUDA
  is unavailable.

All trained backends produce JSON checkpoints that `autoctx hermes
recommend --advisor` can consume.

## What the advisor predicts

Per the shipped AC-708/AC-709 path, the initial advisor predicts Curator
actions from exported decision rows:

- `added`, `archived`, `consolidated`, or `pruned` labels,
- confidence and per-label metrics from the local training run,
- protected-skill status for pinned, bundled, and hub-owned skills.

None of these mutate Hermes state. They are evidence + scores; Curator
and the operator decide what to do with them.
