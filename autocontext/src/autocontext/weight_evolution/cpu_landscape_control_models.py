"""Pinned M19 budget-limited CPU landscape contracts, separate from M18."""

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

LandscapeControlFamily = Literal["informative-improvement-v1", "misleading-improvement-v1", "neutral-v1"]
LANDSCAPE_CONTROL_FAMILIES: tuple[LandscapeControlFamily, ...] = (
    "informative-improvement-v1", "misleading-improvement-v1", "neutral-v1",
)
LANDSCAPE_CONTROL_ROSTER: tuple[tuple[str, LandscapeControlFamily, int, int], ...] = tuple(
    (f"task-{2 * index + instance:02}", family, instance, 1900000 + 2 * index + instance)
    for index, family in enumerate(LANDSCAPE_CONTROL_FAMILIES) for instance in (1, 2)
)
LANDSCAPE_CONTROL_TASK_SEEDS = {task_id: seed for task_id, _, _, seed in LANDSCAPE_CONTROL_ROSTER}
# Fixed integers, never a search for favorable draws. Repeats and option-name
# permutations are nested within task instances, not independent replications.
LANDSCAPE_CONTROL_REPEATS = (
    CpuCampaignRepeat(repeat_id="repeat-01", inference_seed=0, search_order_seed=0),
    CpuCampaignRepeat(repeat_id="repeat-02", inference_seed=1, search_order_seed=1),
)
LANDSCAPE_CONTROL_ROLES = ("practice", "confirmation", "audit")
MAX_LANDSCAPE_CONTROL_BYTES = 4 * 1024 * 1024


def landscape_base_pairs(instance_index: int) -> tuple[tuple[int, int], ...]:
    """Two declared teachers/corruption margins, independent of any policy result."""
    if type(instance_index) is not int or instance_index not in (1, 2):
        raise ValueError("landscape instance must be one or two")
    margins = (12, 14, 3, 5) if instance_index == 1 else (9, 2, 4, 6)
    correct_classes = {0, 1} if instance_index == 1 else {0}
    labels = (0, 1, 0, 1) if instance_index == 1 else (1, 0, 1, 0)
    pairs = []
    for feature_class, (margin, label) in enumerate(zip(margins, labels, strict=True)):
        winner = label if feature_class in correct_classes else 1 - label
        pairs.append((margin, 0) if winner == 0 else (0, margin))
    return (*pairs, (0, 0), (0, 0), (0, 0), (0, 0))


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


class CpuLandscapeControlCampaignTask(_Pinned):
    task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    family: LandscapeControlFamily
    instance_index: int = Field(ge=1, le=2)
    bundle_lock_sha256: Digest
    bundle_lock_size_bytes: int = Field(gt=0, le=MAX_LANDSCAPE_CONTROL_BYTES)
    provenance_sha256: Digest
    provenance_size_bytes: int = Field(gt=0, le=MAX_LANDSCAPE_CONTROL_BYTES)


class CpuLandscapeControlCampaignSpec(_Pinned):
    schema_version: Literal[1] = 1
    study_kind: Literal["generated_cpu_search_landscapes_v1"] = "generated_cpu_search_landscapes_v1"
    campaign_id: Identifier = Field(max_length=96)
    tasks: tuple[CpuLandscapeControlCampaignTask, ...] = Field(min_length=6, max_length=6)
    repeats: tuple[CpuCampaignRepeat, ...] = LANDSCAPE_CONTROL_REPEATS
    policies: tuple[CampaignPolicy, ...] = CAMPAIGN_POLICIES
    scorer: Literal["exact-label-v1"] = "exact-label-v1"
    stopping_rule: Literal["complete-schedule-one-confirmation-look-v1"] = "complete-schedule-one-confirmation-look-v1"
    reporting_rule: Literal["finite-landscape-calibration-not-generalization-v1"] = (
        "finite-landscape-calibration-not-generalization-v1"
    )
    sampling_unit: Literal["task-instance"] = "task-instance"
    oracle_scope: Literal["direct-logits-fixed-seeds-not-stochastic-generalization"] = (
        "direct-logits-fixed-seeds-not-stochastic-generalization"
    )
    budget: CpuCampaignBudget
    implementation_digest: Digest
    oracle_sha256: Digest
    oracle_size_bytes: int = Field(gt=0, le=MAX_LANDSCAPE_CONTROL_BYTES)
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False

    @model_validator(mode="after")
    def fixed_design(self) -> Self:
        if tuple((t.task_id, t.family, t.instance_index, t.generation_seed) for t in self.tasks) != LANDSCAPE_CONTROL_ROSTER:
            raise ValueError("landscape controls require the exact six task instances")
        if self.repeats != LANDSCAPE_CONTROL_REPEATS or self.policies != CAMPAIGN_POLICIES or self.budget.max_runs != 36:
            raise ValueError("landscape controls require two fixed repeats and three policies, exactly 36 searches")
        if len({task.bundle_lock_sha256 for task in self.tasks}) != 6:
            raise ValueError("landscape control task bundles repeat")
        return self


class CpuLandscapeControlCampaignLock(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["cpu_search_landscape_campaign_bundle_v1"] = "cpu_search_landscape_campaign_bundle_v1"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=MAX_LANDSCAPE_CONTROL_BYTES)


class CpuLandscapeControlTaskProvenance(_Pinned):
    schema_version: Literal[1] = 1
    generator: Literal["sha256-search-landscapes-v1"] = "sha256-search-landscapes-v1"
    generator_source_sha256: Digest
    family: LandscapeControlFamily
    task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    instance_index: int = Field(ge=1, le=2)
    cases_per_role: Literal[8] = 8
    feature_indices: tuple[int, ...] = Field(min_length=8, max_length=8)
    base_weights: tuple[float, ...] = Field(min_length=16, max_length=16)
    teacher_labels: tuple[int, ...] = Field(min_length=4, max_length=4)
    teacher_rule: Literal["instance-specific-balanced-four-class-teacher-v1"] = (
        "instance-specific-balanced-four-class-teacher-v1"
    )
    option_order_rule: Literal["three-directions-interleaved-step-one-before-step-two-v1"] = (
        "three-directions-interleaved-step-one-before-step-two-v1"
    )
    neutral_anchor_rule: Literal["zero-logit-context-and-top-k-one-decoding-v1"] = (
        "zero-logit-context-and-top-k-one-decoding-v1"
    )
    heldout_shift: Literal["none"] = "none"
    split_identity_rule: Literal["normalized-token-count-ray-v1"] = "normalized-token-count-ray-v1"
    expected_joint_status: Literal["vetoed"] = "vetoed"
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
            raise ValueError("landscape control features must permute eight coordinates")
        expected = [0.0] * 16
        for feature, pair in zip(self.feature_indices, landscape_base_pairs(self.instance_index), strict=True):
            expected[feature * 2:feature * 2 + 2] = pair
        labels = (0, 1, 0, 1) if self.instance_index == 1 else (1, 0, 1, 0)
        if tuple(expected) != self.base_weights or self.teacher_labels != labels:
            raise ValueError("landscape base logits or teacher differ from the declared instance")
        return self
