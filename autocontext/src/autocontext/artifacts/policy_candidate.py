"""Immutable trace-derived skill candidate contract (AC-1019).

Executable payloads use SkillReference; lifecycle stays in ContextBundleStore.
JSON strings keep nested legacy models immutable without changing their schema.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autocontext.artifacts.models import ArtifactProvenance, PolicyArtifact
from autocontext.context_bundles.models import canonical_json, stable_digest
from autocontext.knowledge.harness_entries import SkillReference


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class CandidateLimits(FrozenContract):
    timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    max_memory_mb: int = Field(default=256, ge=16, le=256, strict=True)
    max_output_bytes: int = Field(default=65536, ge=1024, le=1048576, strict=True)

    def require_within(self, caller: CandidateLimits) -> None:
        if any(value > getattr(caller, key) for key, value in self.model_dump().items()):
            raise ValueError("candidate resource request exceeds caller limits")


DEFAULT_CANDIDATE_LIMITS = CandidateLimits()


class TraceEvidence(FrozenContract):
    trace_id: str = Field(min_length=1, max_length=160)
    group_id: str = Field(min_length=1, max_length=160)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed: int = Field(strict=True)
    role: Literal["successful_training", "training_counterexample"]


class CandidateManifestBase(FrozenContract):
    """Shared immutable payload; specialized manifests own runtime and evidence types."""

    scenario: str = Field(min_length=1)
    scenario_version: str = Field(min_length=1)
    input_schema: str
    output_schema: str
    applicability: str
    skill_json: str
    provenance_json: str
    dependencies: tuple[str, ...] = ()
    capability_grants: tuple[str, ...] = ()
    limits: CandidateLimits = Field(default_factory=CandidateLimits)
    synthesis_provider: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    served_model: str | None
    prompt_version: str
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluator_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation_refs: tuple[str, ...]
    synthesis_usage_json: str = "{}"

    @model_validator(mode="after")
    def validate_payloads(self) -> CandidateManifestBase:
        skill = SkillReference.model_validate_json(self.skill_json)
        provenance = ArtifactProvenance.model_validate_json(self.provenance_json)
        if skill.entrypoint != "choose_action" or provenance.scenario != self.scenario:
            raise ValueError("candidate payload does not match its scenario/entrypoint")
        if self.dependencies or self.capability_grants:
            raise ValueError("v1 candidates have no external dependencies or capability grants")
        for field in ("skill_json", "provenance_json", "input_schema", "output_schema", "applicability", "synthesis_usage_json"):
            value = getattr(self, field)
            if canonical_json(json.loads(value)) != value:
                raise ValueError(f"{field} must use canonical JSON")
        return self

    @property
    def skill(self) -> SkillReference:
        """Return a fresh legacy payload; callers cannot mutate the manifest."""
        return SkillReference.model_validate_json(self.skill_json)

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.skill.source.encode()).hexdigest()

    @property
    def digest(self) -> str:
        return stable_digest(self.model_dump(mode="json"))

    def require_digest(self, expected: str) -> None:
        if self.digest != expected:
            raise ValueError("candidate manifest digest mismatch")


class PolicyCandidateManifest(CandidateManifestBase):
    # Preserve the AC-1019 v1 wire fields and digest; no migration is required.
    schema_version: Literal["autocontext.policy-candidate.v1"] = "autocontext.policy-candidate.v1"
    runtime: Literal["python>=3.11;isolated-policy-v1"] = "python>=3.11;isolated-policy-v1"
    source_traces: tuple[TraceEvidence, ...] = Field(min_length=2)

    def to_policy_artifact(self) -> PolicyArtifact:
        """Portable discovery payload only; this does not publish or activate it."""
        return PolicyArtifact(
            id=self.digest,
            name="Trace-derived scoped policy candidate",
            version=1,
            scenario=self.scenario,
            source_code=self.skill.source,
            provenance=ArtifactProvenance.model_validate_json(self.provenance_json),
            tags=["inactive-candidate", self.schema_version],
        )


def json_payload(value: Any) -> str:
    return canonical_json(value.model_dump(mode="json") if isinstance(value, BaseModel) else value)
