#!/usr/bin/env bash
# Run `autoctx solve` against each entry in a sweep manifest and capture results.
#
# Usage:
#   scripts/escalation-sweep/run_sweep.sh <manifest.json> <output_dir> [--gens N] [--timeout SEC]
#
# Writes one <identifier>.out.json per entry (structured solve output or error
# payload) and one <identifier>.meta.json with {identifier, exit_code,
# elapsed_seconds, workspace_root}. A final <output_dir>/index.json lists all
# runs.
#
# Provider: defaults to `claude-cli` (uses the authenticated `claude` binary
# on PATH — no Anthropic API key needed). Override with
# AUTOCONTEXT_AGENT_PROVIDER=... if you prefer `anthropic`, `agent_sdk`, etc.
# Those modes need the provider-specific credential in the environment.
#
# Prerequisites:
#   - `autoctx` on PATH (or run from the autocontext/ source dir)
#   - For claude-cli provider: `claude` CLI installed and authenticated
#   - For anthropic/agent_sdk: ANTHROPIC_API_KEY exported

set -euo pipefail

: "${AUTOCONTEXT_AGENT_PROVIDER:=claude-cli}"
export AUTOCONTEXT_AGENT_PROVIDER

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <manifest.json> <output_dir> [--gens N] [--timeout SEC]" >&2
  exit 2
fi

MANIFEST=$1
OUTPUT_DIR=$2
shift 2

GENS=2
TIMEOUT=600

while [[ $# -gt 0 ]]; do
  case $1 in
    --gens) GENS=$2; shift 2 ;;
    --timeout) TIMEOUT=$2; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2; exit 1
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)
SWEEP_ROOT=$(cd "$OUTPUT_DIR/.." && pwd)
WORKSPACES_DIR="$SWEEP_ROOT/workspaces"
mkdir -p "$WORKSPACES_DIR"

COUNT=$(jq 'length' "$MANIFEST")
echo "sweeping $COUNT scenarios from $MANIFEST → $OUTPUT_DIR" >&2
echo "  provider=$AUTOCONTEXT_AGENT_PROVIDER gens=$GENS timeout=${TIMEOUT}s" >&2
echo "  isolated_workspaces=$WORKSPACES_DIR" >&2

INDEX=()
for i in $(seq 0 $((COUNT - 1))); do
  ID=$(jq -r ".[$i].identifier" "$MANIFEST")
  DESC_FILE=$(mktemp)
  jq -r ".[$i].description" "$MANIFEST" > "$DESC_FILE"

  OUT_JSON="$OUTPUT_DIR/${ID}.out.json"
  META_JSON="$OUTPUT_DIR/${ID}.meta.json"
  WORKSPACE_DIR="$WORKSPACES_DIR/$ID"

  rm -rf "$WORKSPACE_DIR"
  mkdir -p \
    "$WORKSPACE_DIR/runs" \
    "$WORKSPACE_DIR/knowledge" \
    "$WORKSPACE_DIR/skills" \
    "$WORKSPACE_DIR/.claude/skills"

  printf "[%d/%d] %s ... " "$((i + 1))" "$COUNT" "$ID" >&2
  START=$(date +%s)
  set +e
  AUTOCONTEXT_DB_PATH="$WORKSPACE_DIR/runs/autocontext.sqlite3" \
  AUTOCONTEXT_RUNS_ROOT="$WORKSPACE_DIR/runs" \
  AUTOCONTEXT_KNOWLEDGE_ROOT="$WORKSPACE_DIR/knowledge" \
  AUTOCONTEXT_SKILLS_ROOT="$WORKSPACE_DIR/skills" \
  AUTOCONTEXT_CLAUDE_SKILLS_PATH="$WORKSPACE_DIR/.claude/skills" \
  AUTOCONTEXT_EVENT_STREAM_PATH="$WORKSPACE_DIR/runs/events.ndjson" \
  AUTOCONTEXT_AUDIT_LOG_PATH="$WORKSPACE_DIR/runs/audit.ndjson" \
  autoctx solve \
    --description "$(cat "$DESC_FILE")" \
    --gens "$GENS" \
    --timeout "$TIMEOUT" \
    --json \
    > "$OUT_JSON" 2>&1
  EXIT=$?
  set -e
  END=$(date +%s)
  ELAPSED=$((END - START))

  jq -n \
    --arg id "$ID" \
    --argjson exit "$EXIT" \
    --argjson elapsed "$ELAPSED" \
    --arg workspace_root "$WORKSPACE_DIR" \
    '{identifier: $id, exit_code: $exit, elapsed_seconds: $elapsed, workspace_root: $workspace_root}' \
    > "$META_JSON"

  INDEX+=("$ID")
  if [[ $EXIT -eq 0 ]]; then
    printf "ok (%ds)\n" "$ELAPSED" >&2
  else
    printf "FAIL exit=%d (%ds)\n" "$EXIT" "$ELAPSED" >&2
  fi

  rm -f "$DESC_FILE"
done

printf '%s\n' "${INDEX[@]}" | jq -R . | jq -s . > "$OUTPUT_DIR/index.json"
echo "wrote $OUTPUT_DIR/index.json" >&2
