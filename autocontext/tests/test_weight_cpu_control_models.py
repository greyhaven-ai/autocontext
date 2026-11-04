from __future__ import annotations

import json
from typing import Any

import pytest

from autocontext.weight_evolution.cpu_campaign_models import CAMPAIGN_POLICIES, CpuCampaignBudget, CpuCampaignRepeat
from autocontext.weight_evolution.cpu_control_models import (
    CONTROL_FAMILIES,
    CONTROL_ROLES,
    CpuControlCampaignLock,
    CpuControlCampaignSpec,
    CpuControlCampaignTask,
    CpuControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_control_tasks import CONTROL_TASK_SEEDS, _task_payloads


@pytest.fixture
def campaign() -> CpuControlCampaignSpec:
    tasks = tuple(CpuControlCampaignTask(task_id=f"task-{index}", generation_seed=CONTROL_TASK_SEEDS[family][0],
                                         family=family, bundle_lock_sha256=str(index) * 64, bundle_lock_size_bytes=200,
                                         provenance_sha256="a" * 64, provenance_size_bytes=1000)
                  for index, family in enumerate(CONTROL_FAMILIES, 1))
    return CpuControlCampaignSpec(campaign_id="control-test", tasks=tasks,
        repeats=(CpuCampaignRepeat(repeat_id="r0", inference_seed=17, search_order_seed=31),),
        budget=CpuCampaignBudget(max_runs=9, max_proposals=81, max_materializations=45, max_inference_calls=5000,
                                 max_input_tokens=70000, max_output_tokens=5000), implementation_digest="b" * 64)


def test_control_contract_is_separate_balanced_and_keeps_original_gate(campaign: CpuControlCampaignSpec) -> None:
    assert CpuControlCampaignSpec.model_validate_json(json.dumps(campaign.to_dict())) == campaign
    assert campaign.policies == CAMPAIGN_POLICIES
    assert campaign.reporting_rule == "descriptive-family-v1"
    assert campaign.stopping_rule == "complete-schedule-one-confirmation-look-v1"
    assert campaign.gpu_execution_deferred and not campaign.promotion_eligible
    assert {task.family for task in campaign.tasks} == set(CONTROL_FAMILIES)


@pytest.mark.parametrize("mutation", ["family", "unbalanced", "policy", "runs", "task-alias", "seed-alias",
                                       "repeat-alias", "reporting-rule", "old-kind", "old-family"])
def test_control_contract_rejects_changed_design(campaign: CpuControlCampaignSpec, mutation: str) -> None:
    data = campaign.to_dict()
    if mutation == "family":
        data["tasks"][0]["family"] = "correct-baseline-v1"
    elif mutation == "unbalanced":
        extra = dict(data["tasks"][0], task_id="extra", generation_seed=9999, bundle_lock_sha256="f" * 64)
        data["tasks"].append(extra)
    elif mutation == "policy":
        data["policies"].reverse()
    elif mutation == "runs":
        data["budget"]["max_runs"] = 8
    elif mutation == "task-alias":
        data["tasks"][1]["task_id"] = data["tasks"][0]["task_id"].upper()
    elif mutation == "seed-alias":
        data["tasks"][1]["generation_seed"] = data["tasks"][0]["generation_seed"]
    elif mutation == "repeat-alias":
        data["repeats"].append(dict(data["repeats"][0]))
    elif mutation == "reporting-rule":
        data["reporting_rule"] = "task-mean-paired-t-8-contrasts-v1"
    elif mutation == "old-kind":
        data["study_kind"] = "generated_cpu_search_campaign"
    else:
        data["tasks"][0]["family"] = "linear-separator-v1"
    with pytest.raises(ValueError):
        CpuControlCampaignSpec.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("field,value", [("schema_version", True), ("gpu_execution_deferred", 1),
                                         ("promotion_eligible", 0)])
def test_control_literal_flags_preserve_types(campaign: CpuControlCampaignSpec, field: str, value: Any) -> None:
    data = campaign.to_dict()
    data[field] = value
    with pytest.raises(ValueError, match="literal type"):
        CpuControlCampaignSpec.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("mutation", ["expected", "missing-role", "teacher", "features", "odd-count", "option-alias",
                                       "scope", "old-generator", "teacher-bias"])
def test_control_provenance_rejects_false_analytical_claims(mutation: str) -> None:
    _, provenance, _ = _task_payloads(task_id="t", generation_seed=1000003,
                                     family="transfer-repair-v1", cases_per_role=8)
    data = provenance.to_dict()
    if mutation == "expected":
        data["expected_behavior"]["designated_weight_accuracy"]["confirmation"] = 0.0
    elif mutation == "missing-role":
        del data["expected_behavior"]["baseline_accuracy"]["audit"]
    elif mutation == "teacher":
        data["teacher_weights"] = [0] * 8
    elif mutation == "features":
        data["feature_indices"] = [0] * 8
    elif mutation == "odd-count":
        data["cases_per_role"] = 7
    elif mutation == "option-alias":
        options = data["expected_behavior"]["designated_weight_option_ids"]
        options[1] = options[0].upper()
    elif mutation == "scope":
        data["expected_behavior"]["scope"] = "guaranteed-joint-confirmation"
    elif mutation == "old-generator":
        data["generator"] = "sha256-linear-separator-v1"
    else:
        data["teacher_bias"] = False
    with pytest.raises(ValueError):
        CpuControlTaskProvenance.model_validate_json(json.dumps(data))


def test_each_family_pins_its_own_expected_pattern() -> None:
    for family in CONTROL_FAMILIES:
        _, provenance, _ = _task_payloads(task_id="t", generation_seed=CONTROL_TASK_SEEDS[family][0],
                                         family=family, cases_per_role=8)
        expected = provenance.expected_behavior
        assert set(expected.baseline_accuracy) == set(expected.designated_weight_accuracy) == set(CONTROL_ROLES)
        assert expected.scope == "analytical-W-calibration-not-joint-gate"
        assert CpuControlTaskProvenance.model_validate_json(json.dumps(provenance.to_dict())) == provenance


@pytest.mark.parametrize("size", [0, 1048577, True])
def test_control_lock_is_byte_bounded(size: int) -> None:
    with pytest.raises(ValueError):
        CpuControlCampaignLock(spec_sha256="a" * 64, spec_size_bytes=size)
