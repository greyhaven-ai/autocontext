"""Pinned M20 ordering and budget controls over the six unchanged M19 tasks."""

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
from autocontext.weight_evolution.cpu_landscape_control_models import (
    LANDSCAPE_CONTROL_ROSTER,
    CpuLandscapeControlTaskProvenance,
    LandscapeControlFamily,
)
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

RobustnessOrder = Literal["canonical", "reverse-lanes"]
RobustnessPanel = Literal["order-comparison", "full-budget"]
RobustnessRounds = Literal[3, 6]
ROBUSTNESS_BASE_TASK_COUNT = 6
ROBUSTNESS_VARIANT_COUNT = 15
ROBUSTNESS_LEGAL_ORDER_COUNT = 90
ROBUSTNESS_OFFLINE_TRAJECTORY_COUNT = 6480
ROBUSTNESS_OFFLINE_BUDGETS = (1, 3, 6)
ROBUSTNESS_POLICY_SEEDS = (
    ("adaptive-v1", 0), ("fixed-order-v1", 0), ("seeded-random-v1", 0), ("seeded-random-v1", 1),
)
ROBUSTNESS_CONTROL_REPEATS = (CpuCampaignRepeat(repeat_id="repeat-01", inference_seed=0, search_order_seed=0),)
MAX_ROBUSTNESS_CONTROL_BYTES = 16 * 1024 * 1024

# The full-budget instances are selected structurally, before any oracle score.
# Orders and budgets are nested conditions, never independent task replications.
ROBUSTNESS_CONTROL_ROSTER: tuple[tuple[str, str, LandscapeControlFamily, int, int, RobustnessOrder,
                                     RobustnessRounds, RobustnessPanel], ...] = (
    *((f"{base}.{order}.r3", base, family, instance, seed, order, 3, "order-comparison")
      for base, family, instance, seed in LANDSCAPE_CONTROL_ROSTER
      for order in ("canonical", "reverse-lanes")),
    *((f"{base}.canonical.r6", base, family, instance, seed, "canonical", 6, "full-budget")
      for base, family, instance, seed in LANDSCAPE_CONTROL_ROSTER if instance == 1),
)


class _Pinned(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", "rounds", "instance_index",
                     "generation_seed", "base_task_count", "legal_order_count", "offline_trajectory_count",
                     mode="before", check_fields=False)
    @classmethod
    def exact_literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        kind = bool if info.field_name in {"gpu_execution_deferred", "promotion_eligible"} else int
        if type(value) is not kind:
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class CpuRobustnessControlCampaignTask(_Pinned):
    task_id: Identifier = Field(max_length=64)
    base_task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    family: LandscapeControlFamily
    instance_index: int = Field(ge=1, le=2)
    order_id: RobustnessOrder
    rounds: Literal[3, 6]
    panel: RobustnessPanel
    bundle_lock_sha256: Digest
    bundle_lock_size_bytes: int = Field(gt=0, le=MAX_ROBUSTNESS_CONTROL_BYTES)
    provenance_sha256: Digest
    provenance_size_bytes: int = Field(gt=0, le=MAX_ROBUSTNESS_CONTROL_BYTES)


class CpuRobustnessControlCampaignSpec(_Pinned):
    schema_version: Literal[1] = 1
    study_kind: Literal["generated_cpu_search_robustness_v1"] = "generated_cpu_search_robustness_v1"
    campaign_id: Identifier = Field(max_length=96)
    tasks: tuple[CpuRobustnessControlCampaignTask, ...] = Field(min_length=15, max_length=15)
    repeats: tuple[CpuCampaignRepeat, ...] = ROBUSTNESS_CONTROL_REPEATS
    policies: tuple[CampaignPolicy, ...] = CAMPAIGN_POLICIES
    scorer: Literal["exact-label-v1"] = "exact-label-v1"
    stopping_rule: Literal["complete-schedule-one-confirmation-look-v1"] = "complete-schedule-one-confirmation-look-v1"
    reporting_rule: Literal["finite-order-budget-robustness-not-generalization-v1"] = (
        "finite-order-budget-robustness-not-generalization-v1"
    )
    sampling_unit: Literal["base-task-instance"] = "base-task-instance"
    base_task_count: Literal[6] = 6
    legal_order_count: Literal[90] = 90
    offline_trajectory_count: Literal[6480] = 6480
    offline_budgets: tuple[int, ...] = ROBUSTNESS_OFFLINE_BUDGETS
    budget: CpuCampaignBudget
    implementation_digest: Digest
    oracle_sha256: Digest
    oracle_size_bytes: int = Field(gt=0, le=MAX_ROBUSTNESS_CONTROL_BYTES)
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False

    @field_validator("offline_budgets", mode="before")
    @classmethod
    def exact_budgets(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(type(item) is not int for item in value):
            raise ValueError("robustness offline budgets require exact integers")
        return tuple(value)

    @model_validator(mode="after")
    def fixed_design(self) -> Self:
        actual = tuple((t.task_id, t.base_task_id, t.family, t.instance_index, t.generation_seed,
                        t.order_id, t.rounds, t.panel) for t in self.tasks)
        if actual != ROBUSTNESS_CONTROL_ROSTER:
            raise ValueError("robustness controls require the exact fifteen declared variants of six base tasks")
        if self.repeats != ROBUSTNESS_CONTROL_REPEATS or self.policies != CAMPAIGN_POLICIES or self.budget.max_runs != 45:
            raise ValueError("robustness controls require one fixed seed and three policies, exactly 45 searches")
        if self.offline_budgets != ROBUSTNESS_OFFLINE_BUDGETS:
            raise ValueError("robustness offline budgets are exactly one, three and six")
        if len({task.bundle_lock_sha256 for task in self.tasks}) != 15:
            raise ValueError("robustness control variant bundles repeat")
        return self


class CpuRobustnessControlCampaignLock(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["cpu_search_robustness_campaign_bundle_v1"] = "cpu_search_robustness_campaign_bundle_v1"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=MAX_ROBUSTNESS_CONTROL_BYTES)


class CpuRobustnessControlTaskProvenance(_Pinned):
    schema_version: Literal[1] = 1
    generator: Literal["preserved-m19-weight-order-budget-v1"] = "preserved-m19-weight-order-budget-v1"
    generator_source_sha256: Digest
    task_id: Identifier = Field(max_length=64)
    base_task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    family: LandscapeControlFamily
    instance_index: int = Field(ge=1, le=2)
    order_id: RobustnessOrder
    rounds: Literal[3, 6]
    panel: RobustnessPanel
    source: CpuLandscapeControlTaskProvenance
    source_bundle_lock_sha256: Digest
    source_bundle_lock_size_bytes: int = Field(gt=0, le=4096)
    source_provenance_sha256: Digest
    source_provenance_size_bytes: int = Field(gt=0, le=65536)
    source_option_ids: tuple[Identifier, ...] = Field(min_length=18, max_length=18)
    source_w_order: tuple[Identifier, ...] = Field(min_length=6, max_length=6)
    w_order: tuple[Identifier, ...] = Field(min_length=6, max_length=6)
    options_digest: Digest
    preservation_rule: Literal["only-W-declaration-order-and-round-budget-change-v1"] = (
        "only-W-declaration-order-and-round-budget-change-v1"
    )
    neutral_anchor_rule: Literal["unchanged-M19-C-and-D-v1"] = "unchanged-M19-C-and-D-v1"
    expected_joint_status: Literal["vetoed"] = "vetoed"
    heldout_shift: Literal["none"] = "none"

    @model_validator(mode="after")
    def pinned_source_and_variant(self) -> Self:
        identity = (self.task_id, self.base_task_id, self.family, self.instance_index, self.generation_seed,
                    self.order_id, self.rounds, self.panel)
        if identity not in ROBUSTNESS_CONTROL_ROSTER:
            raise ValueError("robustness provenance is not one of the declared variants")
        if (self.source.task_id, self.source.family, self.source.instance_index, self.source.generation_seed) != (
            self.base_task_id, self.family, self.instance_index, self.generation_seed
        ):
            raise ValueError("robustness source provenance does not match its base task")
        expected = self.source_w_order if self.order_id == "canonical" else tuple(
            self.source_w_order[index] for index in (2, 1, 0, 5, 4, 3))
        if self.w_order != expected or len(set(self.source_option_ids)) != 18 or len(set(self.source_w_order)) != 6:
            raise ValueError("robustness weight order changed")
        if self.source_option_ids[6:12] != self.source_w_order:
            raise ValueError("robustness source weight identities changed")
        return self
