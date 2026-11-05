"""Pinned M18 measured joint-control campaign, separate from the M17 pilot."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticBudget
from autocontext.weight_evolution.cpu_failure_v2_models import CpuFailureContractV2
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

JOINT_CAMPAIGN_SEARCH_COUNT = 18
JOINT_CAMPAIGN_REFERENCE_COUNT = 6
JOINT_CAMPAIGN_SLOT_COUNT = JOINT_CAMPAIGN_SEARCH_COUNT + JOINT_CAMPAIGN_REFERENCE_COUNT
JOINT_CAMPAIGN_PROFILE: Literal["measured-v2"] = "measured-v2"


class _Pinned(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", "retry_permitted",
                     "refund_permitted", mode="before", check_fields=False)
    @classmethod
    def exact_literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        expected = int if info.field_name == "schema_version" else bool
        if type(value) is not expected:
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class CpuJointCampaignSpec(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["measured-cpu-joint-campaign-v1"] = "measured-cpu-joint-campaign-v1"
    campaign_id: Identifier = Field(max_length=96)
    profile: Literal["measured-v2"] = JOINT_CAMPAIGN_PROFILE
    control_campaign_digest: Digest
    control_lock_sha256: Digest
    control_lock_size_bytes: int = Field(gt=0, le=4096)
    roster_digest: Digest
    schedule_digest: Digest
    implementation_digest: Digest
    execution_implementation_digest: Digest
    native_runtime_digest: Digest
    measured_profile_digest: Digest
    failure_contract: CpuFailureContractV2 = Field(default_factory=CpuFailureContractV2)
    search_budget: CpuCampaignBudget
    reference_budget: CpuDiagnosticBudget
    reference_rule: Literal["all-18-searches-completed-and-published-before-6-practice-references"] = (
        "all-18-searches-completed-and-published-before-6-practice-references"
    )
    dispatch_rule: Literal["18-searches-6-reference-decisions-no-claimed-child-redispatch"] = (
        "18-searches-6-reference-decisions-no-claimed-child-redispatch"
    )
    accounting_rule: Literal["full-claimed-child-capacity-no-refunds-blocked-reference-zero-charge"] = (
        "full-claimed-child-capacity-no-refunds-blocked-reference-zero-charge"
    )
    reporting_rule: Literal["separate-evidence-completion-joint-calibration-and-audit-routing-v1"] = (
        "separate-evidence-completion-joint-calibration-and-audit-routing-v1"
    )
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False
    retry_permitted: Literal[False] = False
    refund_permitted: Literal[False] = False

    @model_validator(mode="after")
    def fixed_schedule(self) -> Self:
        if (self.search_budget.max_runs != JOINT_CAMPAIGN_SEARCH_COUNT
                or self.reference_budget.max_references != JOINT_CAMPAIGN_REFERENCE_COUNT):
            raise ValueError("measured joint campaign requires 18 searches and 6 conditional references")
        return self


class CpuJointCampaignLock(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["measured-cpu-joint-campaign-bundle-v1"] = "measured-cpu-joint-campaign-bundle-v1"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=128 * 1024)
    runtime_sha256: Digest
    runtime_size_bytes: int = Field(gt=0, le=256 * 1024)
