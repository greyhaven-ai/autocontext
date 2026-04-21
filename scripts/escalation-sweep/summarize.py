#!/usr/bin/env python3
"""Classify sweep results into failure buckets and print a summary.

Reads `<output_dir>/index.json` (list of identifiers) produced by run_sweep.sh
and the per-scenario .out.json / .meta.json pairs, then tallies into known
failure buckets. The authoritative signal is the structured JSON object
emitted by the CLI. For current and historical sweep captures, `.out.json`
may contain extra stderr chatter around that object, so this script scans
bottom-up for the last JSON object and classifies from that payload.

Buckets:
    success                       — solve completed, generations executed
    llm_fallback_fired            — success + AC-580 LLM fallback engaged
    spec_quality_threshold        — AC-585: quality_threshold outside (0, 1]
    judge_auth_failure            — AC-586: judge couldn't resolve provider auth
    classifier_low_confidence     — LowConfidenceError raised
    designer_intent_drift         — validate_intent rejected the spec
    designer_parse_exhausted      — AC-575 retry window exhausted
    spec_validation_other         — spec/source/execution validation (non-qt)
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
    ("spec_quality_threshold", re.compile(r"quality_threshold must be between", re.I)),
    (
        "judge_auth_failure",
        re.compile(
            r"could not resolve authentication method|expected either api_key or auth_token",
            re.I,
        ),
    ),
    ("classifier_low_confidence", re.compile(r"LowConfidenceError|family.*confidence.*<.*threshold", re.I)),
    ("designer_intent_drift", re.compile(r"intent validation failed", re.I)),
    ("designer_parse_exhausted", re.compile(r"parse(?:_| )retry exhausted|designer parse failed.*attempt 3/3", re.I)),
    ("spec_validation_other", re.compile(r"(spec|source|execution) validation failed", re.I)),
    ("claude_cli_timeout", re.compile(r"timed? ?out|PiCLIRuntime failed:.*timeout|claude.?cli.*timeout", re.I)),
    ("scenario_execution_failed", re.compile(r"solve did not complete|generation.*fail|executor error", re.I)),
]


def classify_error(message: str) -> str:
    if not message:
        return "unknown"
    for bucket, pattern in BUCKET_PATTERNS:
        if pattern.search(message):
            return bucket
    return "unknown"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def extract_structured_payload(out_path: Path) -> dict:
    """Pull the CLI's structured JSON payload out of .out.json.

    Current and historical sweep captures may merge stderr chatter into
    `.out.json`. To stay resilient, scan bottom-up for the last JSON object.
    """
    if not out_path.exists():
        return {}
    raw = out_path.read_text().strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


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
        meta = read_json(output_dir / f"{ident}.meta.json") or {}
        exit_code = meta.get("exit_code", -1)
        elapsed = meta.get("elapsed_seconds", -1)
        out_path = output_dir / f"{ident}.out.json"
        out_payload = extract_structured_payload(out_path)

        if exit_code == 0:
            bucket = "success"
            detail = out_payload.get("scenario_name", "")
            if out_payload.get("llm_classifier_fallback_used") is True:
                bucket = "llm_fallback_fired"
        else:
            # Trust only the structured `error` field from the extracted JSON
            # payload. This ignores stderr chatter like retry warnings that can
            # otherwise cause misclassification.
            message = str(out_payload.get("error", "")) if out_payload else ""
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
