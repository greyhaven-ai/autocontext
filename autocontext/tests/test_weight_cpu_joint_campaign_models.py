from __future__ import annotations

import json
from typing import Any

import pytest

from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticBudget
from autocontext.weight_evolution.cpu_joint_campaign_models import CpuJointCampaignLock, CpuJointCampaignSpec


def spec() -> CpuJointCampaignSpec:
    return CpuJointCampaignSpec(campaign_id="fixture", control_campaign_digest="a" * 64,
        control_lock_sha256="b" * 64, control_lock_size_bytes=256, roster_digest="c" * 64,
        schedule_digest="d" * 64, implementation_digest="e" * 64, execution_implementation_digest="f" * 64,
        native_runtime_digest="0" * 64, measured_profile_digest="1" * 64,
        search_budget=CpuCampaignBudget(max_runs=18, max_proposals=648, max_materializations=360,
            max_inference_calls=14976, max_input_tokens=120000, max_output_tokens=14976),
        reference_budget=CpuDiagnosticBudget(max_references=6, max_materializations=120,
            max_inference_calls=2496, max_input_tokens=16000, max_output_tokens=2496))


def test_fixed_cpu_only_joint_campaign_contract_is_distinct_and_frozen() -> None:
    value = spec()
    assert CpuJointCampaignSpec.model_validate_json(json.dumps(value.to_dict())) == value
    assert value.profile == "measured-v2" and value.failure_contract.schema_version == 2
    assert value.implementation_digest != value.execution_implementation_digest
    assert value.gpu_execution_deferred and not value.promotion_eligible
    assert not value.retry_permitted and not value.refund_permitted
    assert value.search_budget.max_runs == 18 and value.reference_budget.max_references == 6
    assert value.reference_rule == "all-18-searches-completed-and-published-before-6-practice-references"
    with pytest.raises(ValueError):
        value.profile = "legacy-v1"


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("gpu_execution_deferred", 1), ("promotion_eligible", 0),
    ("retry_permitted", 0), ("refund_permitted", 0), ("gpu_execution_deferred", False),
    ("promotion_eligible", True), ("retry_permitted", True), ("refund_permitted", True),
    ("profile", "legacy-v1"), ("profile", ["measured-v2"]), ("campaign_id", "../elsewhere"),
    ("roster_digest", "unknown"), ("schedule_digest", "a" * 63), ("implementation_digest", "A" * 64),
    ("control_lock_size_bytes", True), ("control_lock_size_bytes", 4097),
    ("reference_rule", "completed-sources-only"), ("dispatch_rule", "retry-once"),
    ("accounting_rule", "refund-unused"), ("reporting_rule", "pool-families"), ("extra", False),
])
def test_contract_rejects_weakened_or_ambiguous_pins(field: str, value: Any) -> None:
    raw = spec().to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        CpuJointCampaignSpec.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("budget,field,value", [
    ("search_budget", "max_runs", 17), ("search_budget", "max_runs", 19),
    ("reference_budget", "max_references", 5), ("reference_budget", "max_references", 7),
    ("search_budget", "max_proposals", True), ("reference_budget", "max_proposals", 1),
    ("reference_budget", "max_proposals", False), ("search_budget", "max_input_tokens", "120000"),
])
def test_contract_keeps_exact_schedule_and_typed_meters(budget: str, field: str, value: Any) -> None:
    raw = spec().to_dict()
    raw[budget][field] = value
    with pytest.raises(ValueError):
        CpuJointCampaignSpec.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field,value", [("schema_version", True), ("spec_size_bytes", 0),
    ("spec_size_bytes", 128 * 1024 + 1), ("runtime_size_bytes", 256 * 1024 + 1),
    ("kind", "paired-cpu-execution-bundle-v1"), ("spec_sha256", "unknown")])
def test_lock_cannot_alias_paired_or_unbounded_assets(field: str, value: Any) -> None:
    raw = {"spec_sha256": "a" * 64, "spec_size_bytes": 100,
           "runtime_sha256": "b" * 64, "runtime_size_bytes": 100}
    raw[field] = value
    with pytest.raises(ValueError):
        CpuJointCampaignLock.model_validate_json(json.dumps(raw))
