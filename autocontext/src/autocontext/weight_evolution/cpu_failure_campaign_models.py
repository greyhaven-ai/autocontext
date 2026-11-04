"""Pins for one observed CPU campaign and its conditional practice references."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationInfo, field_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticBudget
from autocontext.weight_evolution.cpu_failure_models import CpuFailureContract
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier


class _Pinned(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        if type(value) is not (int if info.field_name == "schema_version" else bool):
            raise ValueError("incorrect literal type")
        return value


class CpuFailureCampaignSpec(_Pinned):
    schema_version: Literal[1] = 1
    study_kind: Literal["observed_cpu_control_campaign"] = "observed_cpu_control_campaign"
    campaign_id: Identifier = Field(max_length=96)
    control_campaign_digest: Digest
    control_lock_sha256: Digest
    control_lock_size_bytes: int = Field(gt=0, le=4096)
    runtime_digest: Digest
    implementation_digest: Digest
    failure_contract: CpuFailureContract = Field(default_factory=CpuFailureContract)
    search_budget: CpuCampaignBudget
    reference_budget: CpuDiagnosticBudget
    dispatch_rule: Literal["one-parent-claim-one-dispatch-no-retry"] = "one-parent-claim-one-dispatch-no-retry"
    reference_rule: Literal["all-source-searches-complete-before-practice-reference"] = (
        "all-source-searches-complete-before-practice-reference"
    )
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False


class CpuFailureCampaignLock(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["observed_cpu_campaign_bundle"] = "observed_cpu_campaign_bundle"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=128 * 1024)
    runtime_sha256: Digest
    runtime_size_bytes: int = Field(gt=0, le=128 * 1024)


class CpuFailureDiagnosticBinding(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["observed_cpu_reference_source"] = "observed_cpu_reference_source"
    supervisor_digest: Digest
    source_report_digest: Digest
    source_ledger_digest: Digest
    diagnostic_digest: Digest
    implementation_digest: Digest


class CpuFailureDiagnosticLock(_Pinned):
    schema_version: Literal[1] = 1
    kind: Literal["observed_cpu_reference_plan"] = "observed_cpu_reference_plan"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=16 * 1024 * 1024)
    binding_sha256: Digest
    binding_size_bytes: int = Field(gt=0, le=8192)
