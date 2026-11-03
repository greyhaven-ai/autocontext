"""Predeclared, bounded CPU search campaigns over generated task instances."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

MAX_CAMPAIGN_FILE_BYTES = 1024 * 1024
CampaignPolicy = Literal["adaptive-v1", "fixed-order-v1", "seeded-random-v1"]
CAMPAIGN_POLICIES: tuple[CampaignPolicy, ...] = ("adaptive-v1", "fixed-order-v1", "seeded-random-v1")


class _CampaignModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", mode="before", check_fields=False)
    @classmethod
    def exact_literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        expected = int if info.field_name == "schema_version" else bool
        if type(value) is not expected:
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class CpuCampaignTask(_CampaignModel):
    task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    family: Literal["linear-separator-v1"] = "linear-separator-v1"
    bundle_lock_sha256: Digest
    bundle_lock_size_bytes: int = Field(gt=0, le=MAX_CAMPAIGN_FILE_BYTES)
    provenance_sha256: Digest
    provenance_size_bytes: int = Field(gt=0, le=MAX_CAMPAIGN_FILE_BYTES)


class CpuCampaignRepeat(_CampaignModel):
    repeat_id: Identifier = Field(max_length=32)
    inference_seed: int = Field(ge=0, le=2**32 - 1)
    search_order_seed: int = Field(ge=0, le=2**32 - 1)


class CpuCampaignBudget(_CampaignModel):
    """Campaign-wide reservations, including the possible audit for every run."""

    max_runs: int = Field(ge=1, le=768)
    max_proposals: int = Field(ge=1, le=36864)
    max_materializations: int = Field(ge=1, le=6144)
    max_inference_calls: int = Field(ge=1, le=1572864)
    max_input_tokens: int = Field(ge=1, le=100663296)
    max_output_tokens: int = Field(ge=1, le=1572864)


class CpuCampaignSpec(_CampaignModel):
    schema_version: Literal[1] = 1
    study_kind: Literal["generated_cpu_search_campaign"] = "generated_cpu_search_campaign"
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False
    campaign_id: Identifier = Field(max_length=128)
    tasks: tuple[CpuCampaignTask, ...] = Field(min_length=2, max_length=32)
    repeats: tuple[CpuCampaignRepeat, ...] = Field(min_length=1, max_length=8)
    policies: tuple[CampaignPolicy, ...] = CAMPAIGN_POLICIES
    scorer: Literal["exact-label-v1"] = "exact-label-v1"
    stopping_rule: Literal["complete-schedule-one-confirmation-look-v1"] = "complete-schedule-one-confirmation-look-v1"
    sampling_unit: Literal["task-instance"] = "task-instance"
    reporting_rule: Literal["task-mean-paired-t-8-contrasts-v1"] = "task-mean-paired-t-8-contrasts-v1"
    confidence_z: float = Field(default=1.959963984540054, allow_inf_nan=False)
    budget: CpuCampaignBudget
    implementation_digest: Digest

    @model_validator(mode="after")
    def predeclared_design(self) -> Self:
        if self.policies != CAMPAIGN_POLICIES:
            raise ValueError("campaign must declare the three policies in their pinned order")
        if self.confidence_z != 1.959963984540054:
            raise ValueError("campaign confidence level is pinned to the reporting rule")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("campaign task IDs repeat")
        if len({task.generation_seed for task in self.tasks}) != len(self.tasks):
            raise ValueError("campaign task generation seeds repeat")
        if len({task.bundle_lock_sha256 for task in self.tasks}) != len(self.tasks):
            raise ValueError("campaign task bundles repeat")
        if len({repeat.repeat_id for repeat in self.repeats}) != len(self.repeats):
            raise ValueError("campaign repeat IDs repeat")
        if len({repeat.inference_seed for repeat in self.repeats}) != len(self.repeats):
            raise ValueError("campaign inference replicate seeds repeat")
        if len({repeat.search_order_seed for repeat in self.repeats}) != len(self.repeats):
            raise ValueError("campaign search-order replicate seeds repeat")
        run_ids = [f"{task.task_id}.{repeat.repeat_id}.{policy}".casefold()
                   for task in self.tasks for repeat in self.repeats for policy in self.policies]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("campaign run IDs alias after composition or filesystem case folding")
        if len(self.tasks) * len(self.repeats) * len(self.policies) > self.budget.max_runs:
            raise ValueError("campaign budget cannot reserve the complete run schedule")
        return self


class CpuCampaignLock(_CampaignModel):
    schema_version: Literal[1] = 1
    kind: Literal["cpu_search_campaign_bundle"] = "cpu_search_campaign_bundle"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=MAX_CAMPAIGN_FILE_BYTES)


class CpuTaskProvenance(_CampaignModel):
    """Generator inputs and semantic identities, without claimed search outcomes."""

    schema_version: Literal[1] = 1
    family: Literal["linear-separator-v1"] = "linear-separator-v1"
    generator: Literal["sha256-linear-separator-v1"] = "sha256-linear-separator-v1"
    generator_source_sha256: Digest
    task_id: Identifier = Field(max_length=64)
    generation_seed: int = Field(ge=0, le=2**32 - 1)
    cases_per_role: int = Field(ge=2, le=16)
    teacher_weights: tuple[int, ...] = Field(min_length=8, max_length=8)
    teacher_bias: Literal[0] = 0
    model_digest: Digest
    base_sha256: Digest
    cases_digest: Digest
    candidates_digest: Digest
    options_digest: Digest
    semantic_digest: Digest
    split_identity_rule: Literal["normalized-token-count-ray-v1"] = "normalized-token-count-ray-v1"

    @field_validator("teacher_weights")
    @classmethod
    def bounded_teacher(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(not 1 <= abs(value) <= 4 for value in values):
            raise ValueError("teacher weights must be nonzero signed integers of magnitude at most four")
        return values

    @field_validator("teacher_bias", mode="before")
    @classmethod
    def integer_bias(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("teacher bias must be an integer")
        return value
