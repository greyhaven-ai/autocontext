#!/usr/bin/env bash
# Run `autoctx solve` against each entry in a sweep manifest and capture results.
#
# Usage:
#   scripts/escalation-sweep/run_sweep.sh <manifest.json> <output_dir> [--iterations N] [--timeout SEC]
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


validate_sweep_identifier() {
  local identifier=${1-}
  if [[ ! "$identifier" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$ ]]; then
    printf 'invalid sweep identifier %q: expected 1-128 ASCII letters, digits, underscores, or hyphens; first character must be alphanumeric\n' \
      "$identifier" >&2
    return 1
  fi
}


resolve_workspace_target() {
  local workspaces_dir=${1-}
  local identifier=${2-}
  local canonical_root
  local candidate
  local canonical_target
  local relative_target

  validate_sweep_identifier "$identifier" || return 1
  if [[ ! -d "$workspaces_dir" ]]; then
    printf 'workspace root is not a directory: %s\n' "$workspaces_dir" >&2
    return 1
  fi
  canonical_root=$(cd -- "$workspaces_dir" && pwd -P) || {
    printf 'unable to canonicalize workspace root: %s\n' "$workspaces_dir" >&2
    return 1
  }
  if [[ -z "$canonical_root" || "$canonical_root" == / ]]; then
    printf 'refusing unsafe workspace root: %s\n' "$canonical_root" >&2
    return 1
  fi

  candidate="$canonical_root/$identifier"
  case "$candidate" in
    "$canonical_root"/*) ;;
    *)
      printf 'workspace target is not under canonical root %s: %s\n' "$canonical_root" "$candidate" >&2
      return 1
      ;;
  esac
  relative_target=${candidate#"$canonical_root"/}
  if [[ "$relative_target" != "$identifier" || "$relative_target" == */* ]]; then
    printf 'workspace target is not an exact child of %s: %s\n' "$canonical_root" "$candidate" >&2
    return 1
  fi
  if [[ -L "$candidate" ]]; then
    printf 'refusing symlink workspace target: %s\n' "$candidate" >&2
    return 1
  fi
  if [[ -e "$candidate" ]]; then
    if [[ ! -d "$candidate" ]]; then
      printf 'refusing non-directory workspace target: %s\n' "$candidate" >&2
      return 1
    fi
    canonical_target=$(cd -- "$candidate" && pwd -P) || {
      printf 'unable to canonicalize workspace target: %s\n' "$candidate" >&2
      return 1
    }
  else
    canonical_target=$candidate
  fi
  if [[ "$canonical_target" != "$canonical_root/$identifier" ]]; then
    printf 'workspace target escapes canonical root %s: %s\n' "$canonical_root" "$canonical_target" >&2
    return 1
  fi

  printf '%s\n' "$canonical_target"
}


# Keep the path-validation helpers directly testable without running a sweep.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

: "${AUTOCONTEXT_AGENT_PROVIDER:=claude-cli}"
export AUTOCONTEXT_AGENT_PROVIDER

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <manifest.json> <output_dir> [--iterations N] [--timeout SEC]" >&2
  exit 2
fi

MANIFEST=$1
OUTPUT_DIR=$2
shift 2

ITERATIONS=2
TIMEOUT=600

while [[ $# -gt 0 ]]; do
  case $1 in
    --iterations) ITERATIONS=$2; shift 2 ;;
    --gens) ITERATIONS=$2; shift 2 ;;
    --timeout) TIMEOUT=$2; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2; exit 1
fi

mkdir -p -- "$OUTPUT_DIR"
OUTPUT_DIR=$(cd -- "$OUTPUT_DIR" && pwd -P)
SWEEP_ROOT=$(cd -- "$OUTPUT_DIR/.." && pwd -P)
EXPECTED_WORKSPACES_DIR="$SWEEP_ROOT/workspaces"
mkdir -p -- "$EXPECTED_WORKSPACES_DIR"
WORKSPACES_DIR=$(cd -- "$EXPECTED_WORKSPACES_DIR" && pwd -P)
if [[ "$WORKSPACES_DIR" != "$EXPECTED_WORKSPACES_DIR" ]]; then
  echo "workspace root resolves outside its expected location: $EXPECTED_WORKSPACES_DIR -> $WORKSPACES_DIR" >&2
  exit 1
fi

if ! COUNT=$(jq -er 'if type == "array" then length else error("manifest must be an array") end' "$MANIFEST"); then
  echo "invalid sweep manifest: expected a JSON array" >&2
  exit 1
fi

# Validate and freeze every destructive-path identifier before the first
# workspace is removed. This also prevents a manifest edit between preflight
# and execution from changing a cleanup target.
IDENTIFIERS=()
for ((i = 0; i < COUNT; i++)); do
  if ! ID=$(jq -er ".[$i].identifier | select(type == \"string\")" "$MANIFEST"); then
    echo "invalid sweep identifier at manifest index $i: expected a string" >&2
    exit 1
  fi
  if ! validate_sweep_identifier "$ID"; then
    echo "invalid sweep identifier at manifest index $i" >&2
    exit 1
  fi
  for ((j = 0; j < i; j++)); do
    if [[ "${IDENTIFIERS[$j]}" == "$ID" ]]; then
      echo "duplicate sweep identifier at manifest index $i: $ID" >&2
      exit 1
    fi
  done
  IDENTIFIERS[$i]=$ID
done

echo "sweeping $COUNT scenarios from $MANIFEST → $OUTPUT_DIR" >&2
echo "  provider=$AUTOCONTEXT_AGENT_PROVIDER iterations=$ITERATIONS timeout=${TIMEOUT}s" >&2
echo "  isolated_workspaces=$WORKSPACES_DIR" >&2

INDEX=()
for ((i = 0; i < COUNT; i++)); do
  ID=${IDENTIFIERS[$i]}
  OUT_JSON="$OUTPUT_DIR/${ID}.out.json"
  META_JSON="$OUTPUT_DIR/${ID}.meta.json"
  if ! WORKSPACE_DIR=$(resolve_workspace_target "$WORKSPACES_DIR" "$ID"); then
    echo "refusing unsafe workspace cleanup target for manifest index $i" >&2
    exit 1
  fi

  rm -rf -- "${WORKSPACE_DIR:?workspace target must not be empty}"
  mkdir -- "$WORKSPACE_DIR"
  CREATED_WORKSPACE_DIR=$(cd -- "$WORKSPACE_DIR" && pwd -P)
  if [[ "$CREATED_WORKSPACE_DIR" != "$WORKSPACE_DIR" ]]; then
    echo "created workspace does not match validated target: $WORKSPACE_DIR -> $CREATED_WORKSPACE_DIR" >&2
    exit 1
  fi
  mkdir -p \
    "$WORKSPACE_DIR/runs" \
    "$WORKSPACE_DIR/knowledge" \
    "$WORKSPACE_DIR/skills" \
    "$WORKSPACE_DIR/.claude/skills"

  DESC_FILE=$(mktemp)
  jq -r ".[$i].description" "$MANIFEST" > "$DESC_FILE"

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
    --iterations "$ITERATIONS" \
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

  rm -f -- "$DESC_FILE"
done

printf '%s\n' "${INDEX[@]}" | jq -R . | jq -s . > "$OUTPUT_DIR/index.json"
echo "wrote $OUTPUT_DIR/index.json" >&2
