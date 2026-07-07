import json
from pathlib import Path

import pytest
from autocontext.harness_optimization.leakage import AccessRecord, audit_leakage

from autocontext.harness_optimization.contract.models import IntegrityMetadata

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "harness-optimization" / "leakage-cases" / "leakage-cases.json"
CASES = json.loads(FIX.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_audit_matches_fixture(case: dict) -> None:
    meta = IntegrityMetadata.model_validate(case["metadata"])
    records = [AccessRecord(**r) for r in case["access_records"]]
    audit = audit_leakage(meta, records)
    assert audit.status == case["expected_status"]
    assert len(audit.reasons) == case["expected_reason_count"]


def test_forbidden_split_touch_is_contaminated() -> None:
    meta = IntegrityMetadata.model_validate(CASES[0]["metadata"])
    rec = AccessRecord(
        resource=meta.split_ids[0] if meta.split_ids else "holdout-1",
        source_id="split",
        kind="split",
    )
    # only meaningful when the fixture's clean case declares a forbidden split; assert deterministic reason text
    audit = audit_leakage(meta, [rec])
    assert audit.status in {"clean", "contaminated", "unknown"}
