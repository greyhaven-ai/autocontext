from __future__ import annotations

import json
from typing import Any

import pytest

from autocontext.weight_evolution.cpu_execution_models import CpuExecutionContract, CpuExecutionUsage, CpuOperationClaim


def contract() -> CpuExecutionContract:
    return CpuExecutionContract(scope="search", design_digest="a" * 64, workflow_digest="a" * 64,
        workflow_implementation_digest="b" * 64, implementation_digest="c" * 64,
        native_runtime_digest="d" * 64, measured_profile_digest="e" * 64,
        capacity=CpuExecutionUsage(proposals=9, materializations=5, inference_calls=208,
                                   input_tokens=1664, output_tokens=208))


def test_execution_contract_is_pinned_immutable_and_cpu_only() -> None:
    value = contract()
    assert CpuExecutionContract.model_validate_json(json.dumps(value.to_dict())) == value
    assert value.scope == "search" and value.workflow_digest == value.design_digest
    assert not value.retry_permitted and not value.refund_permitted and not value.promotion_eligible
    assert value.gpu_execution_deferred and value.failure_contract.schema_version == 2
    with pytest.raises(ValueError):
        value.scope = "reference"


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("gpu_execution_deferred", 1), ("promotion_eligible", 0),
    ("retry_permitted", True), ("refund_permitted", True), ("scope", "gpu"),
    ("workflow_digest", "f" * 64), ("native_runtime_digest", "unknown"),
    ("undeclared", "anything"),
])
def test_contract_rejects_unsafe_or_ambiguous_fields(field: str, value: Any) -> None:
    data = contract().to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        CpuExecutionContract.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("field,value", [
    ("proposals", True), ("materializations", -1), ("materializations", 9),
    ("inference_calls", 2049), ("output_tokens", 2049), ("input_tokens", 131073),
    ("proposals", 1.0), ("input_tokens", "32"), ("extra", 0),
])
def test_usage_is_exact_bounded_five_meter_data(field: str, value: Any) -> None:
    raw = CpuExecutionUsage().to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        CpuExecutionUsage.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field,value", [("proposals", 0), ("materializations", 0),
    ("inference_calls", 0), ("input_tokens", 1), ("output_tokens", 207)])
def test_full_workflow_capacity_cannot_omit_reserved_work(field: str, value: int) -> None:
    data = contract().to_dict()
    data["capacity"][field] = value
    with pytest.raises(ValueError):
        CpuExecutionContract.model_validate_json(json.dumps(data))


def test_reference_capacity_has_no_proposals_and_separate_design_identity() -> None:
    raw = contract().to_dict()
    raw.update(scope="reference", workflow_digest="f" * 64)
    with pytest.raises(ValueError, match="proposals"):
        CpuExecutionContract.model_validate_json(json.dumps(raw))
    raw["capacity"]["proposals"] = 0
    value = CpuExecutionContract.model_validate_json(json.dumps(raw))
    assert value.workflow_digest != value.design_digest


@pytest.mark.parametrize("field,value", [("schema_version", True), ("index", True), ("index", -1),
    ("index", 256), ("kind", "edit"), ("study_digest", "a" * 64)])
def test_new_claim_is_bounded_and_cannot_masquerade_as_native_journal(field: str, value: Any) -> None:
    raw = {"schema_version": 1, "contract_digest": contract().digest, "index": 0, "kind": "proposal",
           "label": "C-proposal-1", "input": {}, "reserved": CpuExecutionUsage(proposals=1).to_dict(),
           "previous_result_digest": "0" * 64}
    raw[field] = value
    with pytest.raises(ValueError):
        CpuOperationClaim.model_validate_json(json.dumps(raw))
