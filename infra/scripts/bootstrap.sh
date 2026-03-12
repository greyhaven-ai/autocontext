#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$ROOT_DIR/autocontext"
uv venv
source .venv/bin/activate
uv sync --group dev

mkdir -p "$ROOT_DIR/runs" "$ROOT_DIR/knowledge" "$ROOT_DIR/skills"
mkdir -p "$ROOT_DIR/.claude/skills"

for skill_file in "$ROOT_DIR"/skills/*.md; do
  [ -e "$skill_file" ] || continue
  ln -sfn "$skill_file" "$ROOT_DIR/.claude/skills/$(basename "$skill_file")"
done

echo "Bootstrap complete. Activate with: source $ROOT_DIR/autocontext/.venv/bin/activate"
