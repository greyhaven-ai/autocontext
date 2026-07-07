import json
from pathlib import Path

import pytest

from autocontext.harness_optimization.contract.models import IntegrityMetadata
from autocontext.harness_optimization.leakage import (
    AccessRecord,
    audit_leakage,
    render_leakage_report,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "harness-optimization" / "leakage-cases" / "leakage-cases.json"
CASES = json.loads(FIX.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_audit_matches_fixture(case: dict) -> None:
    meta = IntegrityMetadata.model_validate(case["metadata"])
    records = [AccessRecord(**r) for r in case["access_records"]]
    audit = audit_leakage(meta, records)
    assert audit.status == case["expected_status"]
    assert len(audit.reasons) == case["expected_reason_count"]


def test_render_report_shows_status_and_forbidden() -> None:
    case = CASES[0]
    meta = IntegrityMetadata.model_validate(case["metadata"])
    audit = audit_leakage(meta, [AccessRecord(**r) for r in case["access_records"]])
    report = render_leakage_report(meta, audit)
    assert f"status: {audit.status}" in report
    assert "forbidden_sources:" in report
    assert "required_sources:" in report
    assert "web_allowlist:" in report
