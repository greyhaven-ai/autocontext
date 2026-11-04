from __future__ import annotations

import json
from typing import Any

import pytest

from autocontext.weight_evolution.cpu_campaign_models import (
    CAMPAIGN_POLICIES,
    CpuCampaignBudget,
    CpuCampaignLock,
    CpuCampaignRepeat,
    CpuCampaignSpec,
    CpuCampaignTask,
)


@pytest.fixture
def campaign_contract() -> CpuCampaignSpec:
    return CpuCampaignSpec(
        campaign_id="campaign-model-test",
        tasks=tuple(CpuCampaignTask(task_id=f"task-{index}", generation_seed=index,
                                    bundle_lock_sha256=str(index) * 64, bundle_lock_size_bytes=256,
                                    provenance_sha256=str(index + 2) * 64, provenance_size_bytes=512)
                    for index in (1, 2)),
        repeats=(CpuCampaignRepeat(repeat_id="repeat-1", inference_seed=1, search_order_seed=11),
                 CpuCampaignRepeat(repeat_id="repeat-2", inference_seed=2, search_order_seed=12)),
        budget=CpuCampaignBudget(max_runs=12, max_proposals=108, max_materializations=60,
                                 max_inference_calls=1248, max_input_tokens=14976, max_output_tokens=1248),
        implementation_digest="0" * 64,
    )


def test_campaign_pins_sampling_reporting_policies_and_deferred_gpu(campaign_contract: CpuCampaignSpec) -> None:
    assert CpuCampaignSpec.model_validate_json(json.dumps(campaign_contract.to_dict())) == campaign_contract
    assert campaign_contract.policies == CAMPAIGN_POLICIES
    assert campaign_contract.sampling_unit == "task-instance"
    assert campaign_contract.scorer == "exact-label-v1"
    assert campaign_contract.stopping_rule == "complete-schedule-one-confirmation-look-v1"
    assert campaign_contract.reporting_rule == "task-mean-paired-t-8-contrasts-v1"
    assert campaign_contract.confidence_z == 1.959963984540054
    assert campaign_contract.gpu_execution_deferred is True
    assert campaign_contract.promotion_eligible is False
    assert len(campaign_contract.digest) == 64


@pytest.mark.parametrize("mutation", [
    "same-task-id", "same-task-seed", "same-task-bundle", "same-repeat-id", "same-inference-seed",
    "same-order-seed", "single-task", "no-repeat", "missing-control", "policy-order", "extra-policy",
    "unbounded-runs", "insufficient-runs", "changed-confidence", "scorer", "sampling-unit", "report-rule",
    "stopping-rule", "boolean-version", "integer-gpu", "integer-promotion", "extra-authority",
])
def test_invalid_campaign_design_is_rejected(campaign_contract: CpuCampaignSpec, mutation: str) -> None:
    data = campaign_contract.to_dict()
    duplicate_task_fields = {"same-task-id": "task_id", "same-task-seed": "generation_seed",
                             "same-task-bundle": "bundle_lock_sha256"}
    duplicate_repeat_fields = {"same-repeat-id": "repeat_id", "same-inference-seed": "inference_seed",
                               "same-order-seed": "search_order_seed"}
    if mutation in duplicate_task_fields:
        field = duplicate_task_fields[mutation]
        data["tasks"][1][field] = data["tasks"][0][field]
    elif mutation in duplicate_repeat_fields:
        field = duplicate_repeat_fields[mutation]
        data["repeats"][1][field] = data["repeats"][0][field]
    elif mutation == "single-task":
        data["tasks"] = data["tasks"][:1]
    elif mutation == "no-repeat":
        data["repeats"] = []
    elif mutation == "missing-control":
        data["policies"] = data["policies"][:-1]
    elif mutation == "policy-order":
        data["policies"] = list(reversed(data["policies"]))
    elif mutation == "extra-policy":
        data["policies"].append("unreviewed-v1")
    elif mutation == "unbounded-runs":
        data["budget"]["max_runs"] = 769
    elif mutation == "insufficient-runs":
        data["budget"]["max_runs"] = 11
    elif mutation == "changed-confidence":
        data["confidence_z"] = 1.64
    elif mutation == "scorer":
        data["scorer"] = "fuzzy-label-v1"
    elif mutation == "sampling-unit":
        data["sampling_unit"] = "prompt"
    elif mutation == "report-rule":
        data["reporting_rule"] = "unadjusted-intervals"
    elif mutation == "stopping-rule":
        data["stopping_rule"] = "stop-on-significance"
    elif mutation == "boolean-version":
        data["schema_version"] = True
    elif mutation == "integer-gpu":
        data["gpu_execution_deferred"] = 1
    elif mutation == "integer-promotion":
        data["promotion_eligible"] = 0
    else:
        data["expected_winner"] = "adaptive-v1"
    with pytest.raises(ValueError):
        CpuCampaignSpec.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("value", [True, -1, 2**32, 1.5, "1"])
def test_campaign_seed_types_and_bounds_are_strict(value: Any) -> None:
    with pytest.raises(ValueError):
        CpuCampaignRepeat.model_validate_json(json.dumps({"repeat_id": "r", "inference_seed": value,
                                                          "search_order_seed": 0}))


def test_campaign_lock_has_a_bounded_exact_contract() -> None:
    lock = CpuCampaignLock(spec_sha256="a" * 64, spec_size_bytes=512)
    assert CpuCampaignLock.model_validate_json(json.dumps(lock.to_dict())) == lock
    with pytest.raises(ValueError):
        CpuCampaignLock.model_validate_json(json.dumps({**lock.to_dict(), "schema_version": True}))
    with pytest.raises(ValueError):
        CpuCampaignLock(spec_sha256="a" * 64, spec_size_bytes=1024 * 1024 + 1)


@pytest.mark.parametrize("alias", ["composition", "task-case", "repeat-case"])
def test_schedule_rejects_composed_and_case_insensitive_path_aliases(
    campaign_contract: CpuCampaignSpec, alias: str,
) -> None:
    value = campaign_contract.to_dict()
    if alias == "composition":
        value["tasks"][0]["task_id"], value["tasks"][1]["task_id"] = "a.b", "a"
        value["repeats"][0]["repeat_id"], value["repeats"][1]["repeat_id"] = "c", "b.c"
    elif alias == "task-case":
        value["tasks"][0]["task_id"], value["tasks"][1]["task_id"] = "Task", "task"
    else:
        value["repeats"][0]["repeat_id"], value["repeats"][1]["repeat_id"] = "Repeat", "repeat"
    with pytest.raises(ValueError, match="run IDs alias"):
        CpuCampaignSpec.model_validate_json(json.dumps(value))
