import json
from pathlib import Path

import pytest

from autocontext.harness_optimization.contract.models import IntegrityMetadata
from autocontext.harness_optimization.leakage import AccessRecord, audit_leakage
from autocontext.harness_optimization.leakage_gate import evaluate_leakage_gate

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "harness-optimization" / "leakage-cases" / "leakage-cases.json"
CASES = [c for c in json.loads(FIX.read_text())["cases"] if "gate" in c]


BY_NAME = {c["name"]: c for c in json.loads(FIX.read_text())["cases"]}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_gate_matches_fixture(case: dict) -> None:
    meta = IntegrityMetadata.model_validate(case["metadata"])
    audit = audit_leakage(meta, [AccessRecord(**r) for r in case["access_records"]])
    decision = evaluate_leakage_gate(audit, meta.mode, meta.prompt_provenance or "")
    assert decision.advance is case["gate"]["expected_advance"]
    assert decision.non_promotion_grade is case["gate"]["expected_non_promotion_grade"]


def test_blocked_verified_rationale_is_exact() -> None:
    case = BY_NAME["holdout_file_touch"]
    meta = IntegrityMetadata.model_validate(case["metadata"])
    audit = audit_leakage(meta, [AccessRecord(**r) for r in case["access_records"]])
    decision = evaluate_leakage_gate(audit, meta.mode, meta.prompt_provenance or "")
    assert decision.rationale == (
        "verified run blocked: leakage status contaminated: forbidden source read: holdout-split (data/holdout.jsonl)"
    )


def test_exploratory_override_rationale_is_exact() -> None:
    case = BY_NAME["exploratory_override"]
    meta = IntegrityMetadata.model_validate(case["metadata"])
    audit = audit_leakage(meta, [AccessRecord(**r) for r in case["access_records"]])
    decision = evaluate_leakage_gate(audit, meta.mode, meta.prompt_provenance or "")
    assert decision.rationale == ("exploratory override: advancing non-promotion-grade regardless of leakage")
