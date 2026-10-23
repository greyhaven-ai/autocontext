"""Fail-closed checks for the temporary Pi release bootstrap approval."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from audit_pi_zod_bootstrap import ADVISORY, unique_object, verify_approval, verify_audit


def report() -> dict:
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "zod": {
                "name": "zod", "severity": "high", "range": "<4.6.6",
                "nodes": ["node_modules/zod"],
                "via": [{"name": "zod", "severity": "high", "url": f"https://github.com/advisories/{ADVISORY}"}],
            },
            "autoctx": {
                "name": "autoctx", "severity": "high", "nodes": ["node_modules/autoctx"],
                "via": ["secure-exec", "zod"],
            },
            "secure-exec": {"name": "secure-exec", "severity": "low", "via": []},
        },
        "metadata": {"vulnerabilities": {"info": 0, "low": 1, "moderate": 0, "high": 2, "critical": 0, "total": 3}},
    }


class AuditTests(unittest.TestCase):
    def test_accepts_only_the_known_advisory_and_aggregate(self) -> None:
        verify_audit(report(), 1)

    def test_rejects_other_audit_results(self) -> None:
        for code in (0, 2, -1):
            with self.subTest(code=code), self.assertRaises(ValueError):
                verify_audit(report(), code)

    def test_rejects_unrelated_or_incomplete_findings(self) -> None:
        changes = [
            lambda r: r.update(error={"code": "EAUDIT"}),
            lambda r: r.update(auditReportVersion=3),
            lambda r: r["vulnerabilities"].pop("zod"),
            lambda r: r["vulnerabilities"]["secure-exec"].update(severity="high"),
            lambda r: r["vulnerabilities"]["secure-exec"].update(via=[{"name": "other", "severity": "high"}]),
            lambda r: r["vulnerabilities"]["zod"]["via"][0].update(url="https://github.com/advisories/GHSA-other"),
            lambda r: r["vulnerabilities"]["zod"].update(via=["autoctx"]),
            lambda r: r["vulnerabilities"]["zod"].update(nodes=["node_modules/other/node_modules/zod"]),
            lambda r: r["vulnerabilities"]["autoctx"].update(via=["zod", "other"]),
            lambda r: r["vulnerabilities"]["zod"].update(severity="critical"),
            lambda r: r["vulnerabilities"]["zod"].update(range="*"),
            lambda r: r["metadata"]["vulnerabilities"].update(high=0),
            lambda r: r["metadata"]["vulnerabilities"].update(total=2),
        ]
        for index, change in enumerate(changes):
            candidate = copy.deepcopy(report())
            change(candidate)
            with self.subTest(index=index), self.assertRaises((ValueError, KeyError, TypeError)):
                verify_audit(candidate, 1)

    def test_rejects_duplicate_report_keys(self) -> None:
        with self.assertRaises(ValueError):
            json.loads('{"high": 2, "high": 0}', object_pairs_hook=unique_object)


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pi = Path(self.temp.name)
        self.package = self.pi / "package.json"
        self.package.write_text('{"dependencies":{"autoctx":"^0.19.0"}}')
        self.lock = self.pi / "package-lock.json"
        self.lock.write_text(json.dumps({"packages": {
            "node_modules/zod": {"version": "3.25.76"},
            "node_modules/autoctx": {"version": "0.19.0"},
        }}))
        self.config = {
            "approved": True, "advisory": ADVISORY,
            "approved_at": "2026-10-23T21:07:22Z", "expires_at": "2026-10-24T21:00:00Z",
            "package_sha256": hashlib.sha256(self.package.read_bytes()).hexdigest(),
            "lock_sha256": hashlib.sha256(self.lock.read_bytes()).hexdigest(),
        }
        self.now = datetime.fromisoformat("2026-10-23T22:00:00Z")

    def test_accepts_approved_unchanged_graph_within_deadline(self) -> None:
        verify_approval(self.config, self.pi, self.now)

    def test_rejects_expired_future_or_extended_approval(self) -> None:
        for now in ("2026-10-24T21:00:00Z", "2026-10-23T21:00:00Z"):
            with self.subTest(now=now), self.assertRaises(ValueError):
                verify_approval(self.config, self.pi, datetime.fromisoformat(now))
        for field, value in (("approved", False), ("expires_at", "2026-10-25T21:00:00Z"),
                             ("approved_at", "2026-10-23T21:07:22"), ("advisory", "other")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_approval({**self.config, field: value}, self.pi, self.now)

    def test_rejects_manifest_or_lock_drift(self) -> None:
        for file in (self.package, self.lock):
            original = file.read_bytes()
            file.write_bytes(original + b" ")
            with self.subTest(file=file.name), self.assertRaises(ValueError):
                verify_approval(self.config, self.pi, self.now)
            file.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
