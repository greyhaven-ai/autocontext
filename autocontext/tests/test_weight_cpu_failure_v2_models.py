from __future__ import annotations

import json
from typing import Any

import pytest

from autocontext.weight_evolution.cpu_failure_v2_models import (
    CpuFailureContractV2,
    CpuFailureObservationsV2,
    CpuFailureRecordV2,
    CpuFailureTextV2,
)
from autocontext.weight_evolution.cpu_failure_v2_record import build_cpu_failure_record_v2


def record() -> CpuFailureRecordV2:
    return build_cpu_failure_record_v2(CpuFailureContractV2(), supervisor_digest="a" * 64, run_id="run-01",
        study_digest="b" * 64, runtime_digest="c" * 64, terminal_claim=None, terminal_result=None,
        exception=RuntimeError("observed parent failure"))


def test_failure_contract_and_record_are_immutable_and_do_not_claim_root_cause() -> None:
    value = record()
    assert CpuFailureRecordV2.model_validate_json(json.dumps(value.to_dict())) == value
    assert value.contract_digest == value.contract.digest
    assert not value.contract.retry_permitted and not value.contract.refund_permitted
    assert not value.contract.causal_retroclaim_permitted and value.contract.gpu_execution_deferred
    assert value.attribution == "terminal-binding-does-not-attribute-failure-to-operation"
    assert all(value is None for value in value.observations.to_dict().values())
    with pytest.raises(ValueError):
        value.run_id = "different"


@pytest.mark.parametrize("field,value", [("schema_version", True), ("gpu_execution_deferred", 1),
    ("retry_permitted", 0), ("refund_permitted", 0), ("causal_retroclaim_permitted", 0),
    ("max_exception_nodes", True), ("max_message_bytes", 8192), ("max_stream_bytes", 8192),
    ("max_exception_nodes", 17), ("max_record_bytes", 1024), ("retry_permitted", True)])
def test_contract_rejects_type_coercion_or_unbounded_and_unsafe_options(field: str, value: Any) -> None:
    data = CpuFailureContractV2().to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        CpuFailureContractV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("mutation", ["contract-pin", "graph-edge", "cycle-flag", "truncation-flag",
    "unreachable", "live-without-graph", "lost-with-graph", "missing-terminal", "type-truncated",
    "raw-prompts-field", "byte-cap", "bool-edge"])
def test_record_rejects_inconsistent_or_unbounded_observations(mutation: str) -> None:
    data = record().to_dict()
    if mutation == "contract-pin":
        data["contract_digest"] = "d" * 64
    elif mutation == "graph-edge":
        data["exceptions"][0]["cause_id"] = 4
    elif mutation == "cycle-flag":
        data["exception_cycle_detected"] = True
    elif mutation == "truncation-flag":
        data["exception_chain_truncated"] = True
    elif mutation == "unreachable":
        data["exceptions"].append(dict(data["exceptions"][0], node_id=1))
    elif mutation == "live-without-graph":
        data["exceptions"] = []
    elif mutation == "lost-with-graph":
        data["observation_kind"] = "observation-lost"
    elif mutation == "missing-terminal":
        data["operation_label"] = "invented"
    elif mutation == "type-truncated":
        data["retained_type_truncated"] = True
    elif mutation == "raw-prompts-field":
        data["prompt"] = "private prompt"
    elif mutation == "byte-cap":
        data["exceptions"][0]["message"]["text"] = "🧪" * 300
    else:
        data["exceptions"][0]["cause_id"] = True
    with pytest.raises(ValueError):
        CpuFailureRecordV2.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("values", [{"returncode": True}, {"returncode": 256}, {"signal": 0},
    {"elapsed_seconds": float("inf")}, {"peak_rss_bytes": -1}, {"returncode": 0, "signal": 9},
    {"returncode": -15, "signal": 9}])
def test_measurements_reject_invalid_values_without_guessing(values: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CpuFailureObservationsV2(**values)


def test_signal_is_never_implicitly_filled_from_returncode() -> None:
    assert CpuFailureObservationsV2(returncode=-9).signal is None
    assert CpuFailureObservationsV2(signal=9).returncode is None
    assert CpuFailureObservationsV2(returncode=-9, signal=9).signal == 9


def test_utf8_byte_limit_is_distinct_from_character_limit() -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        CpuFailureTextV2(text="🧪" * 1025)


@pytest.mark.parametrize("values", [
    {"text": "visible", "omitted": True, "omission_reason": "redaction-collision", "redacted": True},
    {"text": "", "omitted": True},
    {"text": "", "omission_reason": "redaction-collision", "redacted": True},
    {"text": "", "omitted": True, "omission_reason": "redaction-collision"},
    {"text": "", "omitted": True, "omission_reason": "rendering-failed"},
    {"text": "", "omitted": 1},
    {"text": "\ud800"},
])
def test_omission_is_explicit_empty_and_strict(values: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CpuFailureTextV2(**values)


@pytest.mark.parametrize("field,value", [("schema_version", 1), ("redaction_marker", "other"),
    ("redaction_rule", "replace-unchecked"), ("kind", "bounded-cpu-failure-observation-v1")])
def test_v2_contract_pins_new_redaction_schema(field: str, value: Any) -> None:
    data = CpuFailureContractV2().to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        CpuFailureContractV2.model_validate_json(json.dumps(data))


def test_total_serialized_byte_cap_is_enforced_after_json_escaping() -> None:
    root = RuntimeError("🧪" * 256)
    current = root
    for _ in range(7):
        current.__cause__ = RuntimeError("🧪" * 256)
        current = current.__cause__
    with pytest.raises(ValueError, match="serialized byte cap"):
        build_cpu_failure_record_v2(CpuFailureContractV2(max_record_bytes=4096),
            supervisor_digest="a" * 64, run_id="bounded", study_digest="b" * 64, runtime_digest="c" * 64,
            terminal_claim=None, terminal_result=None, exception=root)
