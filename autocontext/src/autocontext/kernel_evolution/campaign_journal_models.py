"""Validated durable models for the kernel campaign journal."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from autocontext.kernel_evolution.generation import (
    KernelGenerationBudgetState,
    KernelGenerationFailure,
)
from autocontext.kernel_evolution.models import StrictModel, canonical_digest

KernelArtifactKind = Literal[
    "manifest",
    "prompt",
    "generation_claim",
    "generation_receipt",
    "generation_failure",
    "evaluation_claim",
    "attempt_link",
    "source",
    "report",
    "attempt",
    "lineage",
    "champion",
    "summary",
    "profile_evidence",
    "audit",
    "other",
]


class KernelGenerationClaim(StrictModel):
    schema_version: Literal["autocontext.kernel-generation-claim/v1"] = (
        "autocontext.kernel-generation-claim/v1"
    )
    run_id: str
    proposal_index: int = Field(ge=1)
    prompt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    system_prompt_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    generator_identity: str = Field(min_length=1)
    created_at: str

    @property
    def claim_id(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class KernelGenerationCallClaim(StrictModel):
    schema_version: Literal["autocontext.kernel-generation-call-claim/v1"] = (
        "autocontext.kernel-generation-call-claim/v1"
    )
    run_id: str
    proposal_index: int = Field(ge=1)
    call_index: int = Field(ge=1)
    created_at: str


class KernelGenerationFailureReceipt(StrictModel):
    schema_version: Literal["autocontext.kernel-generation-failure-receipt/v1"] = (
        "autocontext.kernel-generation-failure-receipt/v1"
    )
    run_id: str
    proposal_index: int = Field(ge=1)
    call_index: int = Field(ge=1)
    failure_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    failure: KernelGenerationFailure

    @model_validator(mode="after")
    def validate_failure(self) -> Self:
        if (
            self.failure.proposal_index != self.proposal_index
            or self.failure.call_index != self.call_index
            or self.failure.failure_id != self.failure_id
        ):
            raise ValueError("generation failure receipt identity is inconsistent")
        return self


class KernelEvaluationClaim(StrictModel):
    schema_version: Literal["autocontext.kernel-evaluation-claim/v1"] = (
        "autocontext.kernel-evaluation-claim/v1"
    )
    run_id: str
    generation: int = Field(ge=0)
    role: Literal["baseline", "candidate"]
    generation_receipt_id: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt_id: str = Field(pattern=r"^attempt_[0-9a-f]{32}$")
    created_at: str

    @model_validator(mode="after")
    def validate_role(self) -> Self:
        if self.role == "baseline" and (self.generation != 0 or self.generation_receipt_id is not None):
            raise ValueError("baseline evaluation claims cannot reference generation receipts")
        if self.role == "candidate" and (self.generation < 1 or self.generation_receipt_id is None):
            raise ValueError("candidate evaluation claims require a generation receipt")
        return self


class KernelGenerationAttemptLink(StrictModel):
    schema_version: Literal["autocontext.kernel-generation-attempt-link/v1"] = (
        "autocontext.kernel-generation-attempt-link/v1"
    )
    run_id: str
    proposal_index: int = Field(ge=1)
    generation_receipt_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt_id: str = Field(pattern=r"^attempt_[0-9a-f]{32}$")
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class KernelRunArtifact(StrictModel):
    kind: KernelArtifactKind
    path: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class KernelRunArtifactIndex(StrictModel):
    schema_version: Literal["autocontext.kernel-artifact-index/v1"] = (
        "autocontext.kernel-artifact-index/v1"
    )
    run_id: str
    artifacts: tuple[KernelRunArtifact, ...]
    generated_at: str


class KernelCampaignStatus(StrictModel):
    schema_version: Literal["autocontext.kernel-campaign-status/v1"] = (
        "autocontext.kernel-campaign-status/v1"
    )
    run_id: str
    status: str
    problem_id: str | None = None
    proposals_requested: int | None = Field(default=None, ge=0)
    proposals_generated: int = Field(default=0, ge=0)
    proposals_evaluated: int = Field(default=0, ge=0)
    attempts_persisted: int = Field(default=0, ge=0)
    champion_attempt_id: str | None = None
    champion_artifact_digest: str | None = None
    generation_budget_id: str | None = None
    generation_budget_state: KernelGenerationBudgetState = Field(default_factory=KernelGenerationBudgetState)
    stop_requested: bool = False
    can_resume: bool
    ambiguity: str | None = None
    artifact_index_path: str


__all__ = [
    "KernelArtifactKind",
    "KernelCampaignStatus",
    "KernelEvaluationClaim",
    "KernelGenerationAttemptLink",
    "KernelGenerationCallClaim",
    "KernelGenerationClaim",
    "KernelGenerationFailureReceipt",
    "KernelRunArtifact",
    "KernelRunArtifactIndex",
]
