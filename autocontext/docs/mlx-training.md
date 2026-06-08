# MLX Host Training Setup (Apple Silicon)

## Overview

autocontext's `autoctx train` command uses [MLX](https://github.com/ml-explore/mlx) to fine-tune local models from exported run data. MLX requires direct access to Apple's Metal GPU framework, which means training must run on the macOS host, not inside a Docker sandbox.

Docker containers on macOS run inside a Linux VM and cannot access Metal. The MLX Python package may install on Linux aarch64, but training cannot complete without a Metal-capable Apple Silicon host. Host-side Python environments also cannot be executed directly from the sandbox when they point to macOS-native binaries.

## Prerequisites

| Component         | Version               | Install                    |
| ----------------- | --------------------- | -------------------------- |
| Apple Silicon Mac | M1/M2/M3/M4           | -                          |
| macOS             | Tahoe (26.x) or later | -                          |
| Homebrew          | Latest                | [brew.sh](https://brew.sh) |
| Python            | 3.12+                 | `brew install python@3.12` |
| uv                | 0.10+                 | `brew install uv`          |

The package requires Python 3.11+, but Homebrew Python 3.12 is the safest host setup for MLX on Apple Silicon.

## Installation

### 1. Install Python and uv

```bash
brew install python@3.12
brew install uv
```

### 2. Sync the MLX dependency group

From the `autocontext/` directory:

```bash
cd <project-root>/autocontext
uv sync --group dev --extra mlx
```

This installs the MLX-specific extras:

- `mlx>=0.30.0`
- `rustbpe>=0.1.0`
- `tiktoken>=0.11.0`
- `safetensors>=0.4.0`

## Running Training

Export JSONL data from completed runs:

```bash
cd <project-root>/autocontext
uv run autoctx export-training-data \
  --scenario grid_ctf \
  --all-runs \
  --output training/grid_ctf.jsonl
```

Run training on the host:

```bash
cd <project-root>/autocontext
uv run autoctx train \
  --scenario grid_ctf \
  --data /absolute/path/to/training/grid_ctf.jsonl \
  --time-budget 300
```

Use absolute paths for `--data`. The CLI resolves relative paths from the current working directory, which may differ from the location that originally produced the training data.

The training loop writes its workspace under `runs/train_<scenario>/` and produces a checkpoint bundle that `MLXProvider` can load for local inference.

### Pretrained fine-tuning (`--backend mlxlm`)

The default `mlx` backend trains a small GPT from scratch. The `mlxlm` backend instead
LoRA/DoRA-fine-tunes a _pretrained_ [mlx-lm](https://github.com/ml-explore/mlx-lm)
model on the curated (and optionally score-conditioned) records, so the model starts
from a strong prior over JSON / numbers / structure. It uses the base model's own
tokenizer with a natural-language prompt/completion and completion-only loss
(`--mask-prompt`).

Install the extra: `uv sync --group dev --extra mlxlm`.

| Flag               | Default                                    | Effect                                                           |
| ------------------ | ------------------------------------------ | ---------------------------------------------------------------- |
| `--backend mlxlm`  | `mlx`                                      | Select the pretrained-finetune backend.                          |
| `--base-model`     | `mlx-community/Qwen2.5-0.5B-Instruct-4bit` | Pretrained mlx-lm model (HF repo or local path).                 |
| `--fine-tune-type` | `lora`                                     | `lora`, `dora` (weight-decomposed, usually stronger), or `full`. |
| `--num-layers`     | `8`                                        | Number of layers to fine-tune (fewer = less memory).             |

```bash
uv run autoctx train --backend mlxlm \
  --data /absolute/path/to/training/grid_ctf.jsonl \
  --base-model mlx-community/Qwen2.5-0.5B-Instruct-4bit \
  --fine-tune-type dora --num-layers 8 \
  --elite-fraction 0.3 --score-conditioned
```

Curation (`--elite-fraction` / `--dedupe`) and `--score-conditioned` apply to this
backend too (score-conditioning is expressed as a natural-language quality directive
in the prompt rather than the `<|quality|>` token). The fine-tuned adapters are written
under the run's `adapters/` directory.

### GRPO / GSPO RLVR (`--backend grpo`)

Where `mlx` / `cuda` / `mlxlm` use the scenario verifier OFFLINE (to filter / condition /
weight a supervised dataset), the `grpo` backend uses it ONLINE as a reward. It wraps
[`mlx-lm-lora`](https://github.com/Goekdeniz-Guelmez/mlx-lm-lora): for each prompt it
samples a group of completions, scores each with the scenario (`execute_match` for game
scenarios, `evaluate_output` for agent tasks), and takes a GRPO-family policy-gradient
step. No labelled answers are needed; the verifier is the reward.

Install the dependency directly (it is not yet a packaged extra):

```bash
uv pip install mlx-lm-lora
```

Run it:

```bash
uv run autoctx train --backend grpo \
  --scenario grid_ctf \
  --base-model mlx-community/Qwen2.5-1.5B-Instruct-4bit \
  --fine-tune-type lora --num-layers 8 \
  --train-steps 100
```

The variant defaults to **GSPO** (sequence-level importance sampling, the recommended
stability fix over vanilla GRPO). The reward, prompt dataset, and a generated reward
file (which delegates to the scenario verifier) are written under the run directory;
LoRA/DoRA adapters land in `adapters/`.

Notes:

- Use a capable base and an in-reach scenario. Small / weak / over-specialized bases hit
  a documented RLVR capability ceiling (no gain or collapse), so a `1.5B`+ instruct base
  is a better starting point than a `0.5B` one.
- The `--base-model`, `--fine-tune-type`, and `--num-layers` flags are shared with the
  `mlxlm` backend; `--train-steps` maps to GRPO iterations.

### R1 recipe: distillation cold-start then RLVR (`train-r1`)

The `mlxlm` (reasoning distillation) and `grpo` (RLVR) backends compose into the R1-style
recipe: SFT a reasoning cold-start, then run verifiable-reward RL _resuming that adapter_
rather than restarting from the base model. `autoctx train-r1` runs both stages
end-to-end as one command:

```bash
uv run autoctx train-r1 \
  --scenario antichain_diverse \
  --data reasoning_traces.jsonl \
  --output-dir runs/r1 \
  --base-model mlx-community/Qwen2.5-3B-Instruct-4bit \
  --variant gspo
```

Stage 1 (distill) trains a LoRA adapter on `--data` under `runs/r1/distill/`; stage 2
(RLVR) runs GRPO/GSPO under `runs/r1/rlvr/`, passing the distilled adapter as
`--resume-adapter-file` so RL builds on the cold-start. If the distillation stage
produces no adapter, RLVR falls back to training from the base model. The `--variant`
flag selects the RLVR algorithm (`gspo` | `grpo` | `dr_grpo` | `dapo`); `--register-import`
registers a consumer-repo scenario inside the RLVR subprocess (the distillation stage
trains on the JSONL directly and needs no scenario). Both stages need `mlx-lm` and
`mlx-lm-lora` installed.

### On-policy distillation (`--backend opd`)

Where `mlxlm` distills OFF-policy (SFT on a fixed teacher-written dataset) and `grpo` uses a
sparse end-of-episode reward, **on-policy distillation** combines the two: the student
samples completions on-policy, and the dense per-token signal is the reverse KL
`KL(student || teacher)` against a frozen teacher on the student's _own_ trajectories
(Agarwal et al. GKD; popularized by Thinking Machines, Oct 2025). Reverse KL is mode-seeking
and cannot be reward-hacked, and the per-token signal is reported to reach the same
reasoning quality as RL at roughly a tenth of the compute.

This is the MLX-native build (mlx-lm-lora has no GKD/distillation mode), so it trains
**in-process** rather than shelling out: it loads a frozen teacher and a LoRA student, has
the student roll out, and steps the student to match the teacher's per-token distribution.

```bash
uv run autoctx train --backend opd \
  --scenario antichain_diverse \
  --base-model mlx-community/Qwen2.5-1.5B-Instruct-4bit \
  --teacher-model mlx-community/Qwen2.5-7B-Instruct-4bit \
  --num-layers 8
```

Notes:

- `--base-model` is the **student** and `--teacher-model` is the teacher (default:
  `mlx-community/Qwen2.5-7B-Instruct-4bit`); both empty fall back to same-family Qwen2.5
  defaults. Teacher and student **must share a tokenizer** (the run aborts with a clear
  error on a vocab mismatch), so keep them in one model family.
- The teacher only needs to be at least as capable as the student; it need not be huge.
- The run stops at the next iteration boundary once `--time-budget` is reached (model
  loading counts against the budget), so it cannot overrun indefinitely.
- For cross-platform / larger runs (Linux, NVIDIA, multi-GPU), the off-the-shelf counterpart
  is TRL's `GKDTrainer` (`lmbda` = on-policy fraction, `beta=1.0` = reverse KL); this `opd`
  backend is the local Apple-Silicon path.

### Tokenizer vocabulary size (optional)

`--vocab-size` (default 8192) sets the BPE tokenizer's target vocab for the from-scratch
`mlx` / `cuda` backends. The model head and embedding auto-size to the resulting
tokenizer vocab (the base vocab plus the structural special-token slots), so this is a
single knob that trades sequence length against subword sharing: a smaller vocab yields
longer token sequences but more shared subwords (often better on small corpora), while a
larger vocab shortens sequences. It must be `>= 256` (the byte-level BPE base). The
`mlxlm` backend rejects a non-default value (it fine-tunes a pretrained model and uses
that model's tokenizer); the chosen size is recorded in `data_stats`.

```bash
uv run autoctx train \
  --scenario grid_ctf \
  --data /absolute/path/to/training/grid_ctf.jsonl \
  --vocab-size 4096
```

### Record curation (optional)

These flags curate the training records before tokenization (defaults are a no-op,
so omitting them reproduces the previous behavior). Curation is applied to the
training split only; the held-out validation split is untouched.

| Flag                      | Default | Range    | Effect                                                                                                                                                                       |
| ------------------------- | ------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--elite-fraction`        | `1.0`   | `(0, 1]` | Train on only the top fraction of records by score (e.g. `0.25` keeps the best quarter). Rejection-sampling fine-tuning: imitate the best of the distribution, not its mean. |
| `--dedupe`                | off     | flag     | Drop duplicate constructions (exact by canonical strategy JSON), keeping the highest-scoring representative.                                                                 |
| `--dedupe-near-threshold` | `1.0`   | `(0, 1]` | With `--dedupe`, also drop near-duplicates at/above this character-shingle Jaccard similarity. `1.0` = exact only; e.g. `0.9` removes near-identical strategies.             |

Out-of-range values are rejected before training starts. Example (train on the
best 20% with exact + near deduplication, and keep the best-by-validation-loss
checkpoint):

```bash
uv run autoctx train \
  --scenario grid_ctf \
  --data /absolute/path/to/training/grid_ctf.jsonl \
  --elite-fraction 0.2 \
  --dedupe --dedupe-near-threshold 0.9 \
  --val-select
```

The published model artifact records the curation settings plus the raw and
curated record counts (`data_stats`) so a trained model's data split is reproducible.

### Symmetry / transform augmentation (optional)

`--augmenter` multiplies the training data through a domain transform: many research
constructions have equivalent variants under a group action (e.g. affine maps over
F_q for cap-sets), each with the same score, so emitting them as extra records is a
cheap, high-leverage data multiplier. The transforms are domain-specific and live in
the consumer repo, not in autocontext core; an augmenter is referenced by a
`"package.module:function"` spec and resolved by dynamic import, keeping core
domain-agnostic.

The augmenter is a callable `list[record] -> list[record]` that returns the expanded
training set (it may cap or pre-dedupe as it sees fit). It runs on the training records
_before_ curation, so `--dedupe` / `--elite-fraction` then prune symmetry-equivalent
duplicates and select the elite over the augmented pool. A malformed spec or an
augmenter that returns a non-list/empty result fails fast. Applies to all backends;
the chosen spec is recorded in `data_stats`.

The module must be importable from where you run `autoctx train` (that directory is
added to the training subprocess `PYTHONPATH`) or otherwise installed / on
`PYTHONPATH`, since the subprocess runs from a generated workspace, not your cwd.

```bash
uv run autoctx train \
  --scenario cap_set \
  --data /absolute/path/to/training/cap_set.jsonl \
  --augmenter my_pkg.symmetry:affine_orbit --dedupe
```

### Score-conditioned generation (optional)

`--score-conditioned` trains the model to map a target quality onto a construction
(Decision-Transformer / Quark style): each training example gets a `<|quality|>`
control token before the strategy, derived from its score (quantized into 5 buckets
over `[0, 1]`). At assessment/inference the model is prompted with the **top** bucket,
steering it toward high-quality outputs rather than the dataset mean.

The control token is **gated**: it is reserved (one extra vocab slot, id appended
last) and registered only for score-conditioned runs, so the default model vocab and
architecture are byte-identical unless the flag is set. The conditioning contract is
persisted in the checkpoint `config.json` (`score_conditioned`, `num_quality_buckets`)
and in the registry `data_stats`, and `MLXProvider` reapplies the top-bucket prompt
automatically when serving a score-conditioned checkpoint. Pairs naturally with
`--elite-fraction` (train on the best, then ask for the best):

```bash
uv run autoctx train \
  --scenario grid_ctf \
  --data /absolute/path/to/training/grid_ctf.jsonl \
  --score-conditioned --elite-fraction 0.3
```

### Reward-weighted loss (optional)

`--loss-weight-by-score` is the soft counterpart to elite filtering (reward-weighted
regression): instead of dropping low-scoring records (`--elite-fraction`) or tagging
them (`--score-conditioned`), every record is kept but its loss is scaled by a weight
derived from its score, so the gradient leans toward high-reward constructions. The
weight is applied **per training example** (each example's mean completion loss is
weighted, then averaged across the batch), so a long completion cannot drown out a
short high-reward one. It applies to the `mlx` and `cuda` backends; the `mlxlm`
backend rejects non-uniform modes.

| Mode      | Meaning                                                                                                                                                                                                    |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `uniform` | Default. Every weight is 1.0 (byte-identical to unweighted training).                                                                                                                                      |
| `linear`  | Min-max maps scores onto `[0.1, 1]` then mean-normalizes. A mild, interpretable tilt.                                                                                                                      |
| `softmax` | `softmax(score / temperature)` then mean-normalizes. `--loss-weight-temperature` (must be `> 0`) is a continuous knob: large flattens toward uniform, small concentrates on the top examples (soft elite). |

All non-uniform modes are mean-normalized to 1.0, so they change the _relative_
emphasis across examples without changing the overall step size. No score spread (all
scores equal) falls back to uniform.

```bash
uv run autoctx train \
  --scenario grid_ctf \
  --data /absolute/path/to/training/grid_ctf.jsonl \
  --loss-weight-by-score softmax --loss-weight-temperature 0.5
```

### Self-improving loop (ReST-EM / Expert Iteration)

`autoctx self-improve` runs the outer loop that turns one-shot distillation into
ReST-EM (a.k.a. expert iteration / PatternBoost): each round trains on the current
dataset, samples constructions from the trained model and scores them in-scenario,
keeps the highest-scoring elite, appends them as new training records, and retrains
on the grown dataset. The model generates its own training data, biased toward the
best of what it can already produce, so quality compounds across rounds. This is the
self-training analogue of rejection-sampling fine-tuning, lifted from a single fit to
an iterated one.

Sampling uses a positive temperature so the collected constructions are diverse
(greedy decoding would collect identical samples and stall the loop). Sample
collection is MLX-only for now, so the loop drives the `mlx` backend.

Because each round trains _before_ appending its own elite, the loop runs one final
training pass over the full accumulated dataset so the shipped model reflects every
collected sample (including the last round's elite). That model lands in
`<output-dir>/final` and its score is reported as `final_avg_score`. Generated elite
records inherit the seed dataset's representative (most common) `context` so every
training example shares the same context prefix, rather than mixing the seed records'
playbook/hints context with empty prefixes for generated examples.

| Flag                  | Default | Meaning                                                 |
| --------------------- | ------- | ------------------------------------------------------- |
| `--rounds`            | `3`     | Number of generate -> filter -> retrain rounds.         |
| `--samples-per-round` | `16`    | Constructions sampled and scored each round.            |
| `--elite-fraction`    | `0.25`  | Top fraction of each round's samples kept and appended. |
| `--train-steps`       | `100`   | Training steps per round.                               |
| `--score-conditioned` | off     | Carry score-conditioning through every round.           |

```bash
uv run autoctx self-improve \
  --scenario grid_ctf \
  --data /absolute/path/to/training/grid_ctf.jsonl \
  --output-dir runs/self_improve \
  --rounds 3 --samples-per-round 16 --elite-fraction 0.25
```

The command prints a per-round table (avg_score, samples generated, elite kept,
growing dataset size), the final model directory + its `final_avg_score`, and writes
the full accumulated dataset to `<output-dir>/final_dataset.jsonl`. Pass `--json` for
the structured `history` plus `final_model_dir` / `final_avg_score` / `best_avg_score`.

## Automating Host Training for Sandboxed Agents

For sandboxed agents, especially OpenClaw agents running in Docker, the cleanest low-risk approach is a file-based host-training bridge.

### Why a File Bridge

- the sandbox cannot access Metal directly
- you do not need to expose a network service
- you do not need to grant broad host exec permissions to the sandbox
- the agent can request training asynchronously and poll for results through the shared workspace

### How It Works

1. The agent writes `request-*.json` into a watched directory.
2. A host-side `launchd` agent notices the file and runs a watcher script.
3. The watcher script invokes `uv run autoctx train` on the host.
4. The watcher writes `<request>-result.json` back to the same directory.
5. The agent polls for the result file and then loads the produced local artifact.

## Request Format

The agent writes a request file such as `request-123.json`:

```json
{
  "scenario": "grid_ctf",
  "data": "/absolute/path/to/training-data.jsonl",
  "time_budget": 60
}
```

## Result Format

Successful run:

```json
{
  "status": "success",
  "scenario": "grid_ctf",
  "timestamp": "2026-03-12T02:49:33Z"
}
```

Failure:

```json
{
  "status": "error",
  "exit_code": 1,
  "scenario": "grid_ctf",
  "timestamp": "2026-03-12T02:49:33Z"
}
```

## Reference Watcher Script

Save as `~/.openclaw/scripts/autocontext-train-watcher.sh`:

```bash
#!/bin/bash
set -euo pipefail

REQUEST_DIR="$HOME/.openclaw/workspace/autocontext/runs/train-requests"
AUTOCTX_DIR="$HOME/.openclaw/workspace/autocontext/autocontext"
LOG="/tmp/autocontext-train-watcher.log"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) watcher triggered" >> "$LOG"

for req in "$REQUEST_DIR"/request-*.json; do
  [ -f "$req" ] || continue
  [[ "$req" == *-result.json ]] && continue
  [ -s "$req" ] || { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) skipping empty file: $req" >> "$LOG"; continue; }

  BASENAME="$(basename "$req" .json)"
  RESULT_FILE="$REQUEST_DIR/${BASENAME}-result.json"

  [ -f "$RESULT_FILE" ] && continue

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) processing $req" >> "$LOG"

  SCENARIO=$(python3.12 -c "import json,sys; print(json.load(open(sys.argv[1]))['scenario'])" "$req" 2>/dev/null || echo "")
  DATA_PATH=$(python3.12 -c "import json,sys; print(json.load(open(sys.argv[1]))['data'])" "$req" 2>/dev/null || echo "")
  TIME_BUDGET=$(python3.12 -c "import json,sys; print(json.load(open(sys.argv[1])).get('time_budget', 60))" "$req" 2>/dev/null || echo "60")

  if [ -z "$SCENARIO" ] || [ -z "$DATA_PATH" ]; then
    echo "{\"status\":\"error\",\"message\":\"missing scenario or data in request\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$RESULT_FILE"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) error: missing fields in $req" >> "$LOG"
    continue
  fi

  cd "$AUTOCTX_DIR"
  if /opt/homebrew/bin/uv run autoctx train --scenario "$SCENARIO" --data "$DATA_PATH" --time-budget "$TIME_BUDGET" >> "$LOG" 2>&1; then
    echo "{\"status\":\"success\",\"scenario\":\"$SCENARIO\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$RESULT_FILE"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) training complete for $SCENARIO" >> "$LOG"
  else
    EXIT_CODE=$?
    echo "{\"status\":\"error\",\"exit_code\":$EXIT_CODE,\"scenario\":\"$SCENARIO\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$RESULT_FILE"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) training failed ($EXIT_CODE) for $SCENARIO" >> "$LOG"
  fi
done
```

Make it executable:

```bash
chmod 755 ~/.openclaw/scripts/autocontext-train-watcher.sh
```

## Reference `launchd` Plist

Save as `~/Library/LaunchAgents/com.autocontext.train-watcher.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.autocontext.train-watcher</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/cirdan/.openclaw/scripts/autocontext-train-watcher.sh</string>
  </array>
  <key>WatchPaths</key>
  <array>
    <string>/Users/cirdan/.openclaw/workspace/autocontext/runs/train-requests</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/autocontext-train-watcher-stdout.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/autocontext-train-watcher-stderr.log</string>
</dict>
</plist>
```

Update the example paths to match your home directory and shared workspace path.

Load the agent:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.autocontext.train-watcher.plist
launchctl list com.autocontext.train-watcher
```

## Bridge Test

Write a request:

```bash
echo '{"scenario": "grid_ctf", "data": "/absolute/path/to/training-data.jsonl", "time_budget": 60}' > ~/.openclaw/workspace/autocontext/runs/train-requests/request-test.json
```

Check logs and result:

```bash
cat /tmp/autocontext-train-watcher.log
cat ~/.openclaw/workspace/autocontext/runs/train-requests/request-test-result.json
```

Clean up:

```bash
rm ~/.openclaw/workspace/autocontext/runs/train-requests/request-test*.json
```

## Alternative Approaches

### Gateway Exec

OpenClaw's host-exec gateway is cleaner in principle, but today it routes all exec traffic to the host rather than only the training command. That is too broad for Slack-style sandboxed agents and makes normal sandbox behavior awkward.

### HTTP Bridge

A localhost HTTP bridge is possible, but it adds a service boundary and local networking complexity without giving much over the file-based trigger model.

## Troubleshooting

### `MLX is required`

You are either running inside Docker or you have not synced the MLX extra on the host:

```bash
uv sync --group dev --extra mlx
```

### Python version errors

Install Homebrew Python and verify it:

```bash
brew install python@3.12
python3.12 --version
```

### Metal runtime failures

MLX requires Apple Silicon and a Metal-capable macOS host. Intel Macs are not supported.

### Watcher does not trigger

Check:

```bash
launchctl list com.autocontext.train-watcher
```

Also verify that:

- the watched directory exists
- request files match `request-*.json`
- the script is executable

### Permission errors on workspace files

If the sandbox created the exported data with restrictive permissions:

```bash
chmod -R u+rw ~/.openclaw/workspace/autocontext/runs/
```
