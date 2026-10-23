#!/usr/bin/env python3
"""Audit Pi with the short-lived, graph-bound Zod release bootstrap approval."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ADVISORY = "GHSA-mrhx-c7xc-m3w5"
SEVERITIES = {"info", "low", "moderate", "high", "critical"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def verify_approval(config: dict[str, Any], pi: Path, now: datetime) -> None:
    require(config.get("approved") is True, "Bootstrap exception is not approved")
    require(config.get("advisory") == ADVISORY, "Unexpected bootstrap advisory")
    approved = datetime.fromisoformat(config["approved_at"])
    expires = datetime.fromisoformat(config["expires_at"])
    require(approved.tzinfo is not None and expires.tzinfo is not None, "UTC offset required")
    require(timedelta(0) < expires - approved <= timedelta(hours=24), "Approval exceeds 24 hours")
    require(approved <= now < expires, "Bootstrap approval is not active or has expired")
    for filename, key in (("package.json", "package_sha256"), ("package-lock.json", "lock_sha256")):
        digest = hashlib.sha256((pi / filename).read_bytes()).hexdigest()
        require(digest == config.get(key), f"Pi {filename} changed; approval no longer applies")
    lock = json.loads((pi / "package-lock.json").read_text(), object_pairs_hook=unique_object)
    require(lock["packages"]["node_modules/zod"]["version"] == "3.25.76", "Unexpected Zod version")
    require(lock["packages"]["node_modules/autoctx"]["version"] == "0.19.0", "Unexpected runtime")


def verify_audit(report: dict[str, Any], returncode: int) -> None:
    require(returncode == 1 and "error" not in report, "Unexpected npm audit result")
    require(report.get("auditReportVersion") == 2, "Unsupported npm audit format")
    findings = report.get("vulnerabilities")
    require(isinstance(findings, dict), "Missing npm audit inventory")
    counts: Counter[str] = Counter()
    serious: set[str] = set()
    for name, finding in findings.items():
        require(finding.get("name") == name, "Invalid finding name")
        severity = finding.get("severity")
        require(severity in SEVERITIES, "Unknown vulnerability severity")
        counts[severity] += 1
        if severity not in {"info", "low"}:
            serious.add(name)
        for via in finding.get("via", []):
            if isinstance(via, dict) and via.get("severity") not in {"info", "low"}:
                require(name == "zod" and via.get("name") == "zod", "Unrelated advisory")
                require(via.get("url") == f"https://github.com/advisories/{ADVISORY}", "Unrelated Zod advisory")
                require(via.get("severity") == "high", "Zod advisory severity changed")
    require(serious == {"autoctx", "zod"}, "Unrelated moderate/high finding or changed inventory")
    zod = findings["zod"]
    require(zod["severity"] == "high" and zod.get("nodes") == ["node_modules/zod"], "Unexpected Zod scope")
    require(len(zod.get("via", [])) == 1 and isinstance(zod["via"][0], dict), "Unexpected Zod advisory inventory")
    require(zod.get("range") == "<4.6.6", "Zod advisory range changed")
    runtime = findings["autoctx"]
    require(runtime["severity"] == "high" and runtime.get("nodes") == ["node_modules/autoctx"], "Unexpected runtime scope")
    require(runtime.get("via") == ["secure-exec", "zod"], "Unexpected runtime advisory inventory")
    metadata = report.get("metadata", {}).get("vulnerabilities", {})
    require(all(type(metadata.get(s)) is int and metadata[s] == counts[s] for s in SEVERITIES), "Incomplete audit counts")
    require(metadata.get("total") == len(findings), "Incomplete audit inventory")


def main() -> int:
    try:
        config = json.loads((ROOT / "scripts/pi_zod_bootstrap_exception.json").read_text(), object_pairs_hook=unique_object)
        verify_approval(config, ROOT / "pi", datetime.now(UTC))
        audit = subprocess.run(
            ["npm", "audit", "--omit=dev", "--audit-level=moderate", "--json"],
            cwd=ROOT / "pi", capture_output=True, text=True, check=False,
        )
        report = json.loads(audit.stdout, object_pairs_hook=unique_object)
        verify_audit(report, audit.returncode)
        print(f"BOOTSTRAP EXCEPTION: Pi Zod {ADVISORY} only; expires {config['expires_at']}")
        print("Verified unchanged Pi manifest/lock and complete audit; all unrelated moderate/high findings rejected.")
        print("Remove this exception immediately after the published-runtime upgrade.")
    except (KeyError, TypeError, ValueError, OSError) as error:
        print(f"Pi audit failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
