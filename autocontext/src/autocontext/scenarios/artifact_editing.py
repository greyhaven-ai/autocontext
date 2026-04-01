"""Artifact-editing scenario family with artifact-based evaluation (AC-248).

Scenarios where agents modify real artifacts (files, configs, schemas,
structured outputs) and are judged on the resulting artifact state and
validation pipeline outcomes, not just prose quality.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class Artifact(BaseModel):
    """A versioned artifact that can be edited."""

    path: str
    content: str
    content_type: str  # e.g., "yaml", "json", "python", "text"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artifact:
        return cls.model_validate(data)


class ArtifactDiff(BaseModel):
    """Records a change to an artifact."""

    path: str
    operation: str  # "create", "modify", "delete"
    before: str | None
    after: str | None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactDiff:
        return cls.model_validate(data)


@dataclass(slots=True)
class ArtifactValidationResult:
    """Result of validating an artifact's state."""

    valid: bool
    errors: list[str]
    warnings: list[str]


class ArtifactEditingResult(BaseModel):
    """Result of evaluating an artifact-editing scenario."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    diffs: list[ArtifactDiff]
    validation: ArtifactValidationResult
    artifacts_modified: int
    artifacts_valid: int

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactEditingResult:
        return cls.model_validate(data)


class ArtifactEditingInterface(ABC):
    """Contract for artifact-editing scenarios.

    Agents modify real artifacts (files, configs, schemas) and are
    evaluated on the resulting artifact state and validation outcomes.
    """

    name: str

    @abstractmethod
    def describe_task(self) -> str:
        """Return a human-readable description of the editing task."""

    @abstractmethod
    def get_rubric(self) -> str:
        """Return the evaluation rubric."""

    @abstractmethod
    def initial_artifacts(self, seed: int | None = None) -> list[Artifact]:
        """Return the initial set of artifacts to be edited."""

    @abstractmethod
    def get_edit_prompt(self, artifacts: list[Artifact]) -> str:
        """Return the editing prompt given the current artifacts."""

    @abstractmethod
    def validate_artifact(self, artifact: Artifact) -> ArtifactValidationResult:
        """Validate a single artifact's state."""

    @abstractmethod
    def evaluate_edits(
        self,
        original: list[Artifact],
        edited: list[Artifact],
    ) -> ArtifactEditingResult:
        """Evaluate the full set of edits."""

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        """Return initial state for registry compatibility."""
        artifacts = self.initial_artifacts(seed)
        return {"artifacts": [a.to_dict() for a in artifacts], "seed": seed or 0}

    def compute_diffs(
        self,
        original: list[Artifact],
        edited: list[Artifact],
    ) -> list[ArtifactDiff]:
        """Compute diffs between original and edited artifact sets."""
        original_by_path = {a.path: a for a in original}
        edited_by_path = {a.path: a for a in edited}

        diffs: list[ArtifactDiff] = []

        # Modifications and deletions
        for path, orig in original_by_path.items():
            if path in edited_by_path:
                ed = edited_by_path[path]
                if orig.content != ed.content:
                    diffs.append(ArtifactDiff(
                        path=path, operation="modify", before=orig.content, after=ed.content,
                    ))
            else:
                diffs.append(ArtifactDiff(
                    path=path, operation="delete", before=orig.content, after=None,
                ))

        # Creations
        for path, ed in edited_by_path.items():
            if path not in original_by_path:
                diffs.append(ArtifactDiff(
                    path=path, operation="create", before=None, after=ed.content,
                ))

        return diffs
