"""Portable artifact schemas for the OpenClaw/ClawHub contract.

Defines Pydantic models for the three artifact types exchanged between autocontext
and external systems: harnesses, code policies, and distilled local models.
Each artifact carries provenance, versioning, and scenario compatibility
metadata so consumers can discover and validate artifacts portably.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ArtifactProvenance(BaseModel):
    """Tracks which run, generation, and settings produced an artifact."""

    run_id: str = Field(..., min_length=1, description="autocontext run that produced this artifact")
    generation: int = Field(..., ge=0, description="Generation index within the run")
    scenario: str = Field(..., min_length=1, description="Scenario the artifact was produced for")
    settings: dict[str, Any] = Field(default_factory=dict, description="Relevant autocontext settings at creation time")


class _ArtifactBase(BaseModel):
    """Shared fields for all artifact types."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="Unique artifact identifier")
    name: str = Field(..., min_length=1, description="Human-readable artifact name")
    version: int = Field(..., ge=1, description="Monotonically increasing version number")
    scenario: str = Field(..., min_length=1, description="Primary scenario this artifact targets")
    provenance: ArtifactProvenance = Field(..., description="Provenance metadata")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    compatible_scenarios: list[str] = Field(default_factory=list, description="Additional compatible scenarios")
    tags: list[str] = Field(default_factory=list, description="Free-form tags for discovery")


class HarnessArtifact(_ArtifactBase):
    """A validation harness — source code that checks strategy correctness.

    Harnesses are synthesized by autocontext and can be published to ClawHub so that
    other agents can validate strategies against known constraints.
    """

    artifact_type: Literal["harness"] = Field(default="harness", frozen=True)
    source_code: str = Field(..., min_length=1, description="Python source code of the harness")
    accuracy: float | None = Field(default=None, ge=0.0, le=1.0, description="Measured accuracy on test suite")
    synthesis_iterations: int | None = Field(default=None, ge=1, description="How many iterations to synthesize")


class PolicyArtifact(_ArtifactBase):
    """A code policy — executable strategy logic for a scenario.

    Policies are the distilled output of autocontext strategy evolution runs.
    They can be shared via ClawHub for benchmarking or warm-starting.
    """

    artifact_type: Literal["policy"] = Field(default="policy", frozen=True)
    source_code: str = Field(..., min_length=1, description="Python source code of the policy")
    heuristic_value: float | None = Field(default=None, description="Heuristic quality score")
    match_results: list[dict[str, Any]] = Field(default_factory=list, description="Summary of match results")


class DistilledModelArtifact(_ArtifactBase):
    """A distilled local model — a smaller model trained on autocontext data.

    These are neural network checkpoints produced by knowledge distillation
    from autocontext strategy evolution trajectories.
    """

    artifact_type: Literal["distilled_model"] = Field(default="distilled_model", frozen=True)
    architecture: str = Field(..., min_length=1, description="Model architecture (e.g. transformer, mlp, cnn)")
    parameter_count: int = Field(..., gt=0, description="Total trainable parameters")
    checkpoint_path: str = Field(..., min_length=1, description="Path or URI to the model checkpoint")
    training_data_stats: dict[str, Any] = Field(
        default_factory=dict, description="Statistics about the training data (samples, epochs, loss, etc.)"
    )


class ArtifactManifest(BaseModel):
    """A collection of artifacts — used for bulk publish/discover operations."""

    harnesses: list[HarnessArtifact] = Field(default_factory=list)
    policies: list[PolicyArtifact] = Field(default_factory=list)
    distilled_models: list[DistilledModelArtifact] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def all_artifacts(self) -> list[_ArtifactBase]:
        """Return all artifacts as a flat list."""
        result: list[_ArtifactBase] = []
        result.extend(self.harnesses)
        result.extend(self.policies)
        result.extend(self.distilled_models)
        return result
