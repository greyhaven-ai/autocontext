"""Fixed paired search and conditional-reference campaign pins."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticBudget
from autocontext.weight_evolution.cpu_failure_v2_models import CpuFailureContractV2
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

EXECUTION_PROFILES: tuple[Literal["legacy-v1"], Literal["measured-v2"]] = ("legacy-v1", "measured-v2")


class _Pinned(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        if type(value) is not (int if info.field_name == "schema_version" else bool):
            raise ValueError("incorrect paired campaign literal type")
        return value


class CpuExecutionCampaignSpec(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["paired-cpu-search-execution-v1"] = "paired-cpu-search-execution-v1"
    campaign_id: Identifier = Field(max_length=96)
    profiles: tuple[Literal["legacy-v1"], Literal["measured-v2"]] = EXECUTION_PROFILES
    control_campaign_digest: Digest
    control_lock_sha256: Digest
    control_lock_size_bytes: int = Field(gt=0, le=4096)
    implementation_digest: Digest
    native_runtime_digest: Digest
    measured_profile_digest: Digest
    failure_contract: CpuFailureContractV2 = Field(default_factory=CpuFailureContractV2)
    search_budget: CpuCampaignBudget
    reference_budget: CpuDiagnosticBudget
    reference_rule: Literal["all-nine-profile-searches-completed-before-three-practice-references"] = (
        "all-nine-profile-searches-completed-before-three-practice-references"
    )
    dispatch_rule: Literal["eighteen-searches-six-reference-decisions-no-claimed-child-redispatch"] = (
        "eighteen-searches-six-reference-decisions-no-claimed-child-redispatch"
    )
    accounting_rule: Literal["full-claimed-child-capacity-no-refunds-blocked-reference-zero-charge"] = (
        "full-claimed-child-capacity-no-refunds-blocked-reference-zero-charge"
    )
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False

    @model_validator(mode="after")
    def bounded_schedule(self) -> Self:
        if self.search_budget.max_runs != 18 or self.reference_budget.max_references != 6:
            raise ValueError("paired campaign requires eighteen searches and six conditional references")
        return self


class CpuExecutionCampaignLock(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["paired-cpu-execution-bundle-v1"] = "paired-cpu-execution-bundle-v1"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=128 * 1024)
    runtime_sha256: Digest
    runtime_size_bytes: int = Field(gt=0, le=256 * 1024)
