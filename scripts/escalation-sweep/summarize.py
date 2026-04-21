#!/usr/bin/env python3
"""Classify sweep results into failure buckets and print a summary.

Reads `<output_dir>/index.json` (list of identifiers) produced by run_sweep.sh
and the per-scenario .out.json / .meta.json pairs, then tallies into known
failure buckets:

    success                       — solve completed, generations executed
    classifier_low_confidence     — LowConfidenceError raised
    designer_parse_failure        — spec JSON parse / validation failures
    designer_intent_drift         — validate_intent rejected the spec
    claude_cli_timeout            — subprocess or provider timeout
    scenario_execution_failed     — generations errored after scenario built
    llm_fallback_fired            — solve succeeded after AC-580 LLM family fallback
    unknown                       — didn't match any known pattern

Usage:
    python summarize.py <output_dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BUCKET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("classifier_low_confidence", re.compile(r"LowConfidenceError|family.*confidence.*<.*threshold", re.I)),
    ("designer_intent_drift", re.compile(r"intent validation failed", re.I)),
    ("designer_parse_failure", re.compile(r"(spec|source|execution) validation failed|parse(?:_| )retry exhausted", re.I)),
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
        out_text = out_path.read_text() if out_path.exists() else ""
        out_payload = read_json(out_path) or {}

        if exit_code == 0:
            bucket = "success"
            detail = out_payload.get("scenario_name", "")
            if out_payload.get("llm_classifier_fallback_used") is True:
                bucket = "llm_fallback_fired"
        else:
            message = ""
            if isinstance(out_payload, dict):
                message = str(out_payload.get("error") or out_text)
            else:
                message = out_text
            bucket = classify_error(message)
            detail = message.splitlines()[0][:140] if message else ""

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
