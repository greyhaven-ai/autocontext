"""Parity test: vendored rules.manifest.json must match the Python runtime.

The manifest is the canonical detector contract authored in the website source
of truth. This test guards the vendored copy against drift in the Python
consumer (``autocontext.sharing.safeguards``): rule order/id/scanner/severity,
the dynamic detectors appended at scan time, the ruleset version, and the
sha256 of every trace-exchange fixture.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from autocontext.sharing.safeguards import RULESET_VERSION, SCAN_RULES

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "src" / "autocontext" / "sharing" / "rules.manifest.json"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "trace_exchange"

# Dynamic detectors appended by scan_content in this exact order:
# _find_high_entropy_spans, then _find_invalid_jsonl_lines (JSONL kinds only).
_DYNAMIC_DETECTORS = (
    ("high-entropy-span", "encoded", "review"),
    ("invalid-jsonl-line", "encoded", "review"),
)


def _load_manifest() -> dict:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _expected_rules() -> list[dict]:
    rules: list[dict] = []
    for index, rule in enumerate(SCAN_RULES):
        rules.append(
            {
                "order": index,
                "id": rule.id,
                "scanner": rule.scanner,
                "severity": rule.severity,
                "dynamic": False,
            }
        )
    for offset, (rule_id, scanner, severity) in enumerate(_DYNAMIC_DETECTORS):
        rules.append(
            {
                "order": len(SCAN_RULES) + offset,
                "id": rule_id,
                "scanner": scanner,
                "severity": severity,
                "dynamic": True,
            }
        )
    return rules


def test_manifest_rules_match_runtime() -> None:
    manifest = _load_manifest()
    expected = _expected_rules()
    actual = manifest["rules"]

    assert len(actual) == len(expected)
    for expected_rule, actual_rule in zip(expected, actual, strict=True):
        assert actual_rule["order"] == expected_rule["order"]
        assert actual_rule["id"] == expected_rule["id"]
        assert actual_rule["scanner"] == expected_rule["scanner"]
        assert actual_rule["severity"] == expected_rule["severity"]
        assert actual_rule["dynamic"] == expected_rule["dynamic"]


def test_manifest_ruleset_version_matches_runtime() -> None:
    manifest = _load_manifest()
    assert manifest["ruleset_version"] == RULESET_VERSION


def test_manifest_fixture_hashes_match() -> None:
    manifest = _load_manifest()
    expected_fixtures = manifest["fixtures"]

    actual_fixtures: dict[str, str] = {}
    for path in sorted(_FIXTURES_DIR.rglob("*")):
        if not path.is_file():
            continue
        relpath = path.relative_to(_FIXTURES_DIR).as_posix()
        actual_fixtures[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()

    assert actual_fixtures == expected_fixtures
