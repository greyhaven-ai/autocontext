"""Separate pinned contracts for constructed CPU transfer controls."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_models import (
    CAMPAIGN_POLICIES,
    CampaignPolicy,
    CpuCampaignBudget,
    CpuCampaignRepeat,
)
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

MAX_CONTROL_FILE_BYTES = 1024 * 1024
ControlFamily = Literal["transfer-repair-v1", "correct-baseline-v1", "practice-shortcut-v1"]
CONTROL_FAMILIES: tuple[ControlFamily, ...] = ("transfer-repair-v1", "correct-baseline-v1", "practice-shortcut-v1")
ControlRole = Literal["practice", "confirmation", "audit"]
CONTROL_ROLES: tuple[ControlRole, ...] = ("practice", "confirmation", "audit")


class _ControlModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "teacher_bias", "gpu_execution_deferred", "promotion_eligible",
                     mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        expected = int if info.field_name in {"schema_version", "teacher_bias"} else bool
        if type(value) is not expected:
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class CpuControlCampaignTask(_ControlModel):
    task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    family: ControlFamily
    bundle_lock_sha256: Digest
    bundle_lock_size_bytes: int = Field(gt=0, le=MAX_CONTROL_FILE_BYTES)
    provenance_sha256: Digest
    provenance_size_bytes: int = Field(gt=0, le=MAX_CONTROL_FILE_BYTES)


class CpuControlCampaignSpec(_ControlModel):
    schema_version: Literal[1] = 1
    study_kind: Literal["cpu_transfer_control_campaign"] = "cpu_transfer_control_campaign"
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False
    campaign_id: Identifier = Field(max_length=128)
    tasks: tuple[CpuControlCampaignTask, ...] = Field(min_length=3, max_length=12)
    repeats: tuple[CpuCampaignRepeat, ...] = Field(min_length=1, max_length=8)
    policies: tuple[CampaignPolicy, ...] = CAMPAIGN_POLICIES
    scorer: Literal["exact-label-v1"] = "exact-label-v1"
    stopping_rule: Literal["complete-schedule-one-confirmation-look-v1"] = "complete-schedule-one-confirmation-look-v1"
    sampling_unit: Literal["task-instance"] = "task-instance"
    reporting_rule: Literal["descriptive-family-v1"] = "descriptive-family-v1"
    budget: CpuCampaignBudget
    implementation_digest: Digest

    @model_validator(mode="after")
    def balanced_predeclared_design(self) -> Self:
        if self.policies != CAMPAIGN_POLICIES:
            raise ValueError("control campaign must retain the three pinned policies")
        counts = [sum(task.family == family for task in self.tasks) for family in CONTROL_FAMILIES]
        if min(counts) < 1 or len(set(counts)) != 1:
            raise ValueError("control campaign requires every family with equal task counts")
        for values, label in (([task.task_id.casefold() for task in self.tasks], "task IDs"),
                              ([task.generation_seed for task in self.tasks], "task generation seeds"),
                              ([task.bundle_lock_sha256 for task in self.tasks], "task bundles"),
                              ([repeat.repeat_id.casefold() for repeat in self.repeats], "repeat IDs"),
                              ([repeat.inference_seed for repeat in self.repeats], "inference seeds"),
                              ([repeat.search_order_seed for repeat in self.repeats], "search-order seeds")):
            if len(set(values)) != len(values):
                raise ValueError(f"control campaign repeats {label}")
        run_ids = [f"{task.task_id}.{repeat.repeat_id}.{policy}".casefold()
                   for task in self.tasks for repeat in self.repeats for policy in self.policies]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("control run IDs alias after composition or case folding")
        if len(run_ids) > self.budget.max_runs:
            raise ValueError("control campaign cannot reserve every scheduled run")
        return self


class CpuControlCampaignLock(_ControlModel):
    schema_version: Literal[1] = 1
    kind: Literal["cpu_transfer_control_campaign_bundle"] = "cpu_transfer_control_campaign_bundle"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=MAX_CONTROL_FILE_BYTES)


class CpuControlExpectedBehavior(_ControlModel):
    """Analytical W calibration facts, never inputs to search policy decisions."""

    scope: Literal["analytical-W-calibration-not-joint-gate"] = "analytical-W-calibration-not-joint-gate"
    baseline_accuracy: dict[ControlRole, float]
    designated_weight_option_ids: tuple[Identifier, ...] = Field(min_length=2, max_length=2)
    designated_weight_accuracy: dict[ControlRole, float]

    @model_validator(mode="after")
    def exact_roles(self) -> Self:
        for accuracies in (self.baseline_accuracy, self.designated_weight_accuracy):
            if set(accuracies) != set(CONTROL_ROLES) or any(value not in {0.0, 0.5, 1.0} for value in accuracies.values()):
                raise ValueError("control expectations require the exact three bounded split accuracies")
        if len({name.casefold() for name in self.designated_weight_option_ids}) != 2:
            raise ValueError("designated control options must be distinct")
        return self


class CpuControlTaskProvenance(_ControlModel):
    schema_version: Literal[1] = 1
    family: ControlFamily
    generator: Literal["sha256-transfer-controls-v1"] = "sha256-transfer-controls-v1"
    generator_source_sha256: Digest
    task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    cases_per_role: int = Field(ge=4, le=16)
    feature_indices: tuple[int, ...] = Field(min_length=8, max_length=8)
    teacher_weights: tuple[int, ...] = Field(min_length=8, max_length=8)
    teacher_bias: Literal[0] = 0
    model_digest: Digest
    base_sha256: Digest
    cases_digest: Digest
    candidates_digest: Digest
    options_digest: Digest
    semantic_digest: Digest
    split_identity_rule: Literal["normalized-token-count-ray-v1"] = "normalized-token-count-ray-v1"
    expected_behavior: CpuControlExpectedBehavior

    @model_validator(mode="after")
    def analytical_family(self) -> Self:
        if self.cases_per_role % 2:
            raise ValueError("control cohorts require an even balanced case count")
        if set(self.feature_indices) != set(range(8)):
            raise ValueError("control feature roles must permute all eight vocabulary coordinates")
        teacher = [0] * 8
        teacher[self.feature_indices[0]], teacher[self.feature_indices[1]] = 2, -2
        if tuple(teacher) != self.teacher_weights:
            raise ValueError("control teacher differs from the declared stable evidence coordinates")
        baseline = 1.0 if self.family == "correct-baseline-v1" else 0.5
        designated = ({role: 0.5 for role in CONTROL_ROLES} if self.family == "correct-baseline-v1" else
                      {role: 1.0 for role in CONTROL_ROLES} if self.family == "transfer-repair-v1" else
                      {"practice": 1.0, "confirmation": 0.0, "audit": 0.0})
        if (self.expected_behavior.baseline_accuracy != {role: baseline for role in CONTROL_ROLES}
                or self.expected_behavior.designated_weight_accuracy != designated):
            raise ValueError("control expectations differ from the declared construction algebra")
        return self
