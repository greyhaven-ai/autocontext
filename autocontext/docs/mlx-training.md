# MLX Host Training Setup (Apple Silicon)

## Overview

AutoContext's `autoctx train` command uses [MLX](https://github.com/ml-explore/mlx) to fine-tune local models from exported run data. MLX requires direct access to Apple's Metal GPU framework, which means training must run on the macOS host, not inside a Docker sandbox.

Docker containers on macOS run inside a Linux VM and cannot access Metal. The MLX Python package may install on Linux aarch64, but training cannot complete without a Metal-capable Apple Silicon host. Host-side Python environments also cannot be executed directly from the sandbox when they point to macOS-native binaries.

## Prerequisites

| Component | Version | Install |
|-----------|---------|---------|
| Apple Silicon Mac | M1/M2/M3/M4 | - |
| macOS | Tahoe (26.x) or later | - |
| Homebrew | Latest | [brew.sh](https://brew.sh) |
| Python | 3.12+ | `brew install python@3.12` |
| uv | 0.10+ | `brew install uv` |

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
