#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR/autocontext"
if [ ! -d ".venv" ]; then
  uv venv
fi
source .venv/bin/activate
uv sync --group dev

export AUTOCONTEXT_AGENT_PROVIDER="${AUTOCONTEXT_AGENT_PROVIDER:-deterministic}"

echo "Running demo generations..."
uv run autoctx run --scenario grid_ctf --gens 3 --run-id demo_grid
uv run autoctx run --scenario othello --gens 2 --run-id demo_othello

echo "Starting dashboard at http://127.0.0.1:8000"
uv run autoctx serve --host 127.0.0.1 --port 8000
