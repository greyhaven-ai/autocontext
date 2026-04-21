#!/usr/bin/env python3
"""Classify sweep results into failure buckets and print a summary.

Reads `<output_dir>/index.json` (list of identifiers) produced by run_sweep.sh
and the per-scenario .out.json / .err.log / .meta.json triples, then tallies
into known failure buckets. The authoritative signal is the ``error`` field
inside .out.json (printed by the CLI as a single-line JSON object on failure);
.err.log is consulted only for scenarios where stdout was empty or malformed.

Buckets:
    success                       — solve completed, generations executed
    llm_fallback_fired            — success + AC-580 LLM fallback engaged
    classifier_low_confidence     — LowConfidenceError raised
    designer_intent_drift         — validate_intent rejected the spec
    spec_quality_threshold        — AC-585: quality_threshold out of (0, 1]
    spec_validation_other         — other spec/source/execution validation
    designer_parse_exhausted      — AC-575 retry window exhausted
    judge_auth_failure            — AC-586: judge provider couldn't auth
    claude_cli_timeout            — subprocess or provider timeout
    scenario_execution_failed     — generations errored after scenario built
    unknown                       — didn't match any known pattern

Usage:
    python summarize.py <output_dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Order matters: first match wins, so put more-specific patterns first.
BUCKET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "spec_quality_threshold",
        re.compile(r"quality_threshold must be between", re.I),
    ),
    (
        "judge_auth_failure",
        re.compile(
            r"could not resolve authentication method|expected either api_key or auth_token",
            re.I,
        ),
    ),
    (
        "classifier_low_confidence",
        re.compile(r"LowConfidenceError|family classification confidence .* < .* threshold", re.I),
    ),
    (
        "designer_intent_drift",
        re.compile(r"intent validation failed", re.I),
    ),
    (
        "designer_parse_exhausted",
        re.compile(r"parse(?:_| )retry exhausted|designer parse failed.*attempt 3/3", re.I),
    ),
    (
        "spec_validation_other",
        re.compile(r"(spec|source|execution) validation failed", re.I),
    ),
    (
        "claude_cli_timeout",
        re.compile(r"timed? ?out|timeout after|claude.?cli.*timeout", re.I),
    ),
    (
        "scenario_execution_failed",
        re.compile(r"solve did not complete|generation.*fail|executor error", re.I),
    ),
]


def classify_error(message: str) -> str:
    if not message:
        return "unknown"
    for bucket, pattern in BUCKET_PATTERNS:
        if pattern.search(message):
            return bucket
    return "unknown"


def extract_error_field(out_path: Path) -> str:
    """Pull the CLI's structured ``error`` field out of .out.json.

    On failure the CLI prints a single-line JSON object with an ``error`` key
    to stdout. stderr (now separated in run_sweep.sh) carries Python
    tracebacks / retry log lines. We trust only the structured field here.
    """
    if not out_path.exists():
        return ""
    raw = out_path.read_text().strip()
    if not raw:
        return ""
    # Success output is multi-line JSON; failure output is single-line
    # "{"error": "..."}" — scan bottom-up for the last line that parses as
    # JSON with an "error" key.
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "error" in payload:
            return str(payload["error"])
    # Whole payload might be valid JSON with no "error" key (success case).
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("error", ""))
    return ""


def extract_success_fields(out_path: Path) -> dict:
    try:
        payload = json.loads(out_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def fallback_log_fired(err_path: Path, out_path: Path) -> bool:
    for path in (err_path, out_path):
        if not path.exists():
            continue
        if "LLM classifier fallback:" in path.read_text():
            return True
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    output_dir = Path(argv[1])
    index_path = output_dir / "index.json"
    if not index_path.exists():
        print(f"no index.json at {index_path}", file=sys.stderr)
        return 2

    identifiers: list[str] = json.loads(index_path.read_text())
    buckets: dict[str, list[str]] = {}
    rows: list[dict] = []

    for ident in identifiers:
        meta_path = output_dir / f"{ident}.meta.json"
        try:
            meta = json.loads(meta_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            meta = {}
        exit_code = meta.get("exit_code", -1)
        elapsed = meta.get("elapsed_seconds", -1)

        out_path = output_dir / f"{ident}.out.json"
        err_path = output_dir / f"{ident}.err.log"

        if exit_code == 0:
            bucket = "success"
            success = extract_success_fields(out_path)
            detail = success.get("scenario_name") or ""
            if fallback_log_fired(err_path, out_path):
                bucket = "llm_fallback_fired"
        else:
            message = extract_error_field(out_path)
            bucket = classify_error(message)
            detail = message.splitlines()[0][:140] if message else "(no error field)"

        rows.append(
            {
                "identifier": ident,
                "bucket": bucket,
                "exit": exit_code,
                "elapsed": elapsed,
                "detail": detail,
            }
        )
        buckets.setdefault(bucket, []).append(ident)

    print("\n=== Per-scenario ===")
    print(f"{'ID':<10} {'BUCKET':<28} {'EXIT':>4} {'SEC':>5}  DETAIL")
    for row in rows:
        print(
            f"{row['identifier']:<10} {row['bucket']:<28} {row['exit']:>4} "
            f"{row['elapsed']:>5}  {row['detail']}"
        )

    print("\n=== Tally ===")
    for bucket in sorted(buckets, key=lambda b: -len(buckets[b])):
        members = buckets[bucket]
        print(f"  {bucket:<28} {len(members):>3}  {', '.join(members)}")

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {"rows": rows, "buckets": {k: len(v) for k, v in buckets.items()}},
            indent=2,
        )
    )
    print(f"\nwrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
