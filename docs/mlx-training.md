# MLX Host Training Setup (Apple Silicon)

## Overview

AutoContext's `autoctx train` command uses [MLX](https://github.com/ml-explore/mlx) to fine-tune local models from escalation run data. MLX requires direct access to Apple's Metal GPU framework, which means training **must run on the macOS host** — not inside a Docker sandbox.

Docker containers on macOS run inside a Linux VM (via Apple's Virtualization framework) and cannot access Metal. The MLX Python package installs on Linux aarch64 but crashes at runtime with no Metal GPU available. The host-side Python venv also can't be executed from within the sandbox because it points to macOS-native binaries (e.g., `/opt/homebrew/opt/python@3.12/bin/python3.12`).

## Prerequisites

| Component | Version | Install |
|-----------|---------|---------|
| Apple Silicon Mac | M1/M2/M3/M4 | — |
| macOS | Tahoe (26.x) or later | — |
| Homebrew | Latest | [brew.sh](https://brew.sh) |
| Python | 3.12+ | `brew install python@3.12` |
| uv | 0.10+ | `brew install uv` |

The `pyproject.toml` specifies `requires-python = ">=3.11"`. The macOS system Python (3.9.6) does not meet this requirement. `uv` discovers installed interpreters automatically but does not install them — Homebrew provides the interpreter, `uv` manages the venv.

## Installation

### 1. Install Python and uv

```bash
brew install python@3.12
brew install uv
```

### 2. Sync the MLX dependency group

From the autocontext Python package directory (where `pyproject.toml` lives):

```bash
cd <project-root>/autocontext
uv sync --group dev --extra mlx
```

This installs the MLX optional dependencies defined in `pyproject.toml`:

- `mlx>=0.30.0` — Apple's ML framework for Apple Silicon
- `rustbpe>=0.1.0` — Fast BPE tokenizer training
- `tiktoken>=0.11.0` — Tokenizer library
- `safetensors>=0.4.0` — Safe tensor serialization

## Running Training

After a discovery run produces training data:

```bash
# Export training data (can run in sandbox or on host)
uv run autoctx export-training-data \
  --scenario grid_ctf \
  --all-runs \
  --output training/grid_ctf.jsonl

# Run training (host only)
uv run autoctx train \
  --scenario grid_ctf \
  --data training/grid_ctf.jsonl \
  --time-budget 60
```

**Important:** Use absolute paths for `--data`. The CLI resolves relative paths from the working directory, which may differ from where the training data was generated.

The command produces a safetensors checkpoint bundle that `MLXProvider` can load. Expect approximately 30 seconds on an M4.

### Using the Trained Model

```bash
AUTOCONTEXT_JUDGE_PROVIDER=mlx \
AUTOCONTEXT_MLX_MODEL_PATH=runs/train_grid_ctf/checkpoints/exp_0 \
uv run autoctx run --scenario grid_ctf --gens 3
```

## Host-Training Bridge (File-Based)

For sandboxed agents (e.g., running inside OpenClaw's Docker sandbox), a file-based bridge allows the agent to trigger host-side training autonomously without gaining direct host exec access.

### How It Works

1. **Agent** writes a request JSON to a watched directory on the shared workspace mount
2. **launchd** detects the new file via `WatchPaths` and fires a watcher script
3. **Watcher script** reads the request, runs `autoctx train` on the host (with Metal access), writes a result JSON
4. **Agent** polls for the result file

No network exposure, no host exec permissions granted to the sandbox.

### Request Format

The agent writes a file matching `request-*.json` to the train-requests directory:

```json
{
  "scenario": "grid_ctf",
  "data": "/absolute/path/to/training-data.jsonl",
  "time_budget": 60
}
```

### Result Format

The watcher writes `<request-basename>-result.json` in the same directory:

```json
{
  "status": "success",
  "scenario": "grid_ctf",
  "timestamp": "2026-03-12T02:49:33Z"
}
```

On failure:

```json
{
  "status": "error",
  "exit_code": 1,
  "scenario": "grid_ctf",
  "timestamp": "2026-03-12T02:49:33Z"
}
```

### Setup

#### Watcher Script

Save to `~/.openclaw/scripts/autocontext-train-watcher.sh`:

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

  # Skip if already processed
  [ -f "$RESULT_FILE" ] && continue

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) processing $req" >> "$LOG"

  SCENARIO=$(python3.12 -c "import json,sys; print(json.load(open(sys.argv[1]))['scenario'])" "$req" 2>/dev/null || echo "")
  DATA_PATH=$(python3.12 -c "import json,sys; print(json.load(open(sys.argv[1]))['data'])" "$req" 2>/dev/null || echo "")
  TIME_BUDGET=$(python3.12 -c "import json,sys; print(json.load(open(sys.argv[1])).get('time_budget', 60))" "$req" 2>/dev/null || echo "60")

  if [ -z "$SCENARIO" ] || [ -z "$DATA_PATH" ]; then
    echo "{\"status\":\"error\",\"message\":\"missing scenario or data in request\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$RESULT_FILE"
    continue
  fi

  cd "$AUTOCTX_DIR"
  if /opt/homebrew/bin/uv run autoctx train --scenario "$SCENARIO" --data "$DATA_PATH" --time-budget "$TIME_BUDGET" >> "$LOG" 2>&1; then
    echo "{\"status\":\"success\",\"scenario\":\"$SCENARIO\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$RESULT_FILE"
  else
    EXIT_CODE=$?
    echo "{\"status\":\"error\",\"exit_code\":$EXIT_CODE,\"scenario\":\"$SCENARIO\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$RESULT_FILE"
  fi
done
```

Make executable: `chmod 755 ~/.openclaw/scripts/autocontext-train-watcher.sh`

#### launchd Plist

Save to `~/Library/LaunchAgents/com.autocontext.train-watcher.plist`:

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
    <string>/Users/YOUR_USER/.openclaw/scripts/autocontext-train-watcher.sh</string>
  </array>
  <key>WatchPaths</key>
  <array>
    <string>/Users/YOUR_USER/.openclaw/workspace/autocontext/runs/train-requests</string>
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

Load: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.autocontext.train-watcher.plist`

Verify: `launchctl list com.autocontext.train-watcher`

## Alternative Approaches

### Gateway Exec (Option B)

OpenClaw supports `tools.exec.host = "gateway"` with allowlist gating. However, this redirects **all** exec commands to the host, breaking normal sandbox operations. Until OpenClaw supports per-command host routing, this is not viable for Slack-based agents.

### HTTP Bridge (Option C)

A lightweight HTTP server on the host accepting training requests on localhost. Adds complexity with no meaningful benefit over the file-based approach for this use case.

## Troubleshooting

### "MLX is required"
Run `uv sync --extra mlx` on the macOS host. Not fixable from inside Docker.

### "failed to map segment from shared object"
MLX installed on Linux but can't load without Metal. Training must run on macOS.

### uv can't find Python 3.11+
Install via Homebrew: `brew install python@3.12`

### Watcher doesn't trigger
Verify: `launchctl list com.autocontext.train-watcher`. Check `train-requests/` exists and request matches `request-*.json` glob.

### Permission errors on workspace files
```bash
chmod -R u+rw ~/.openclaw/workspace/autocontext/runs/
```
