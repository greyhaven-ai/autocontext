"""Separate, fixed M18 contracts for seed-conditional joint CPU controls."""

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

JointControlFamily = Literal["joint-positive-v1", "joint-correct-baseline-v1", "joint-practice-only-v1"]
JOINT_CONTROL_FAMILIES: tuple[JointControlFamily, ...] = (
    "joint-positive-v1", "joint-correct-baseline-v1", "joint-practice-only-v1",
)
JOINT_CONTROL_TASK_SEEDS: dict[JointControlFamily, int] = dict(zip(JOINT_CONTROL_FAMILIES, (1800001, 1800002, 1800003),
                                                                 strict=True))
# The first two integers, never a search for favorable sampler draws. Results
# are conditional on this finite seed roster, not stochastic-generalization evidence.
JOINT_CONTROL_REPEATS = (
    CpuCampaignRepeat(repeat_id="repeat-01", inference_seed=0, search_order_seed=0),
    CpuCampaignRepeat(repeat_id="repeat-02", inference_seed=1, search_order_seed=1),
)
JOINT_CONTROL_ROLES = ("practice", "confirmation", "audit")
MAX_JOINT_CONTROL_BYTES = 1024 * 1024


class _Pinned(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        if type(value) is not (int if info.field_name == "schema_version" else bool):
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class CpuJointControlCampaignTask(_Pinned):
    task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    family: JointControlFamily
    bundle_lock_sha256: Digest
    bundle_lock_size_bytes: int = Field(gt=0, le=MAX_JOINT_CONTROL_BYTES)
    provenance_sha256: Digest
    provenance_size_bytes: int = Field(gt=0, le=MAX_JOINT_CONTROL_BYTES)


class CpuJointControlCampaignSpec(_Pinned):
    schema_version: Literal[1] = 1
    study_kind: Literal["generated_joint_cpu_controls_v1"] = "generated_joint_cpu_controls_v1"
    campaign_id: Identifier = Field(max_length=96)
    tasks: tuple[CpuJointControlCampaignTask, ...] = Field(min_length=3, max_length=3)
    repeats: tuple[CpuCampaignRepeat, ...] = JOINT_CONTROL_REPEATS
    policies: tuple[CampaignPolicy, ...] = CAMPAIGN_POLICIES
    scorer: Literal["exact-label-v1"] = "exact-label-v1"
    stopping_rule: Literal["complete-schedule-one-confirmation-look-v1"] = "complete-schedule-one-confirmation-look-v1"
    reporting_rule: Literal["seed-conditional-joint-calibration-v1"] = "seed-conditional-joint-calibration-v1"
    sampling_unit: Literal["task-instance"] = "task-instance"
    oracle_scope: Literal["direct-logits-fixed-seeds-not-stochastic-generalization"] = (
        "direct-logits-fixed-seeds-not-stochastic-generalization"
    )
    budget: CpuCampaignBudget
    implementation_digest: Digest
    oracle_sha256: Digest
    oracle_size_bytes: int = Field(gt=0, le=MAX_JOINT_CONTROL_BYTES)
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False

    @model_validator(mode="after")
    def fixed_design(self) -> Self:
        expected = [(f"task-{i + 1:02}", family, JOINT_CONTROL_TASK_SEEDS[family])
                    for i, family in enumerate(JOINT_CONTROL_FAMILIES)]
        if [(t.task_id, t.family, t.generation_seed) for t in self.tasks] != expected:
            raise ValueError("joint controls require the exact three task instances")
        if self.repeats != JOINT_CONTROL_REPEATS or self.policies != CAMPAIGN_POLICIES or self.budget.max_runs != 18:
            raise ValueError("joint controls require two fixed repeats and three policies, exactly 18 searches")
        if len({task.bundle_lock_sha256 for task in self.tasks}) != 3:
            raise ValueError("joint control task bundles repeat")
        return self


class CpuJointControlCampaignLock(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["joint_cpu_control_campaign_bundle_v1"] = "joint_cpu_control_campaign_bundle_v1"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=MAX_JOINT_CONTROL_BYTES)


class CpuJointControlTaskProvenance(_Pinned):
    schema_version: Literal[1] = 1
    generator: Literal["sha256-joint-controls-v1"] = "sha256-joint-controls-v1"
    generator_source_sha256: Digest
    family: JointControlFamily
    task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    cases_per_role: Literal[8] = 8
    feature_indices: tuple[int, ...] = Field(min_length=8, max_length=8)
    base_weights: tuple[float, ...] = Field(min_length=16, max_length=16)
    teacher_rule: Literal["balanced-feature-class-stable-label-v1"] = "balanced-feature-class-stable-label-v1"
    heldout_shift: Literal["none", "correct-everywhere", "repair-corruption-absent-after-practice"]
    split_identity_rule: Literal["normalized-token-count-ray-v1"] = "normalized-token-count-ray-v1"
    expected_joint_status: Literal["confirmed", "vetoed"]
    oracle_scope: Literal["fixed-seeds-0-and-1-no-seed-or-outcome-filtering"] = (
        "fixed-seeds-0-and-1-no-seed-or-outcome-filtering"
    )
    model_digest: Digest
    base_sha256: Digest
    cases_digest: Digest
    candidates_digest: Digest
    options_digest: Digest
    semantic_digest: Digest

    @model_validator(mode="after")
    def fixed_construction(self) -> Self:
        if set(self.feature_indices) != set(range(8)):
            raise ValueError("joint control features must permute eight coordinates")
        expected = [0.0] * 16
        for feature, pair in zip(self.feature_indices, ((16, 0), (0, 16), (0, 1), (1, 0), (2, 0), (0, 2), (0, 0), (0, 0)),
                                 strict=True):
            expected[feature * 2:feature * 2 + 2] = pair
        if tuple(expected) != self.base_weights:
            raise ValueError("joint control base logits differ from the construction")
        shift = {"joint-positive-v1": "none", "joint-correct-baseline-v1": "correct-everywhere",
                 "joint-practice-only-v1": "repair-corruption-absent-after-practice"}[self.family]
        status = "confirmed" if self.family == "joint-positive-v1" else "vetoed"
        if self.heldout_shift != shift or self.expected_joint_status != status:
            raise ValueError("joint control family expectations changed")
        return self
