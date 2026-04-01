"""Distilled model registry and training artifact publication (AC-287 + AC-288).

First-class registry for distilled model artifacts with active-model
selection by scenario, backend, and runtime type. Training completions
publish artifacts and register them automatically.

Key types:
- DistilledModelRecord: registry entry with activation state
- DistilledModelArtifact: published artifact with training metadata
- TrainingCompletionOutput: enriched output from training runs
- ModelRegistry: register, activate, deactivate, resolve, list
- resolve_model(): deterministic lookup with manual override support
- publish_training_output(): creates artifact + registers into registry
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autocontext.util.json_io import read_json, write_json

_VALID_STATES = frozenset({"candidate", "active", "disabled", "deprecated"})


class DistilledModelRecord(BaseModel):
    """Registry entry for a distilled model artifact."""

    artifact_id: str
    scenario: str
    scenario_family: str
    backend: str  # mlx, cuda, etc.
    checkpoint_path: str
    runtime_types: list[str]  # provider, pi, judge
    activation_state: str  # candidate, active, disabled, deprecated
    training_metrics: dict[str, Any]
    provenance: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.activation_state not in _VALID_STATES:
            raise ValueError(
                f"Invalid activation_state {self.activation_state!r}; expected one of {sorted(_VALID_STATES)}",
            )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DistilledModelRecord:
        return cls.model_validate(data)


class DistilledModelArtifact(BaseModel):
    """Published artifact with training and architecture metadata."""

    artifact_id: str
    checkpoint_path: str
    backend: str
    scenario: str
    parameter_count: int
    architecture: str
    training_metrics: dict[str, Any]
    data_stats: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DistilledModelArtifact:
        return cls.model_validate(data)


@dataclass(slots=True)
class TrainingCompletionOutput:
    """Enriched output from a training run for artifact publication."""

    run_id: str
    checkpoint_path: str
    backend: str
    scenario: str
    scenario_family: str = ""
    parameter_count: int = 0
    architecture: str = ""
    training_metrics: dict[str, Any] = field(default_factory=dict)
    data_stats: dict[str, Any] = field(default_factory=dict)
    runtime_types: list[str] = field(default_factory=lambda: ["provider"])
    metadata: dict[str, Any] = field(default_factory=dict)


def _deterministic_artifact_id(completion: TrainingCompletionOutput) -> str:
    """Generate a deterministic artifact ID from training output."""
    key = f"{completion.run_id}:{completion.checkpoint_path}:{completion.backend}:{completion.scenario}"
    return f"distilled-{hashlib.sha256(key.encode()).hexdigest()[:12]}"


def _runtime_slots_overlap(left: list[str], right: list[str]) -> bool:
    """Return True when two records compete for at least one runtime slot."""
    if not left or not right:
        return True
    return not set(left).isdisjoint(right)


def _artifact_dir(root: Path) -> Path:
    return root / "_openclaw_artifacts"


def _artifact_path(root: Path, artifact_id: str) -> Path:
    return _artifact_dir(root) / f"{artifact_id}.json"


class ModelRegistry:
    """JSON-file registry for distilled model artifacts."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "model_registry"
        self._dir.mkdir(parents=True, exist_ok=True)

    def register(self, record: DistilledModelRecord) -> Path:
        path = self._dir / f"{record.artifact_id}.json"
        write_json(path, record.to_dict())
        return path

    def load(self, artifact_id: str) -> DistilledModelRecord | None:
        path = self._dir / f"{artifact_id}.json"
        if not path.exists():
            return None
        return DistilledModelRecord.from_dict(read_json(path))

    def list_all(self) -> list[DistilledModelRecord]:
        return [
            DistilledModelRecord.from_dict(read_json(p))
            for p in sorted(self._dir.glob("*.json"))
        ]

    def list_for_scenario(self, scenario: str) -> list[DistilledModelRecord]:
        return [r for r in self.list_all() if r.scenario == scenario]

    def activate(self, artifact_id: str) -> None:
        """Activate a model, deactivating any other active model for same slot."""
        target = self.load(artifact_id)
        if target is None:
            raise ValueError(f"Artifact {artifact_id} not found")

        # Deactivate previous active entries for the same scenario+backend+runtime slot.
        for rec in self.list_all():
            if (
                rec.artifact_id != artifact_id
                and rec.scenario == target.scenario
                and rec.backend == target.backend
                and rec.activation_state == "active"
                and _runtime_slots_overlap(rec.runtime_types, target.runtime_types)
            ):
                rec.activation_state = "disabled"
                self.register(rec)

        target.activation_state = "active"
        self.register(target)

    def deactivate(self, artifact_id: str) -> None:
        rec = self.load(artifact_id)
        if rec is None:
            raise ValueError(f"Artifact {artifact_id} not found")
        rec.activation_state = "disabled"
        self.register(rec)


def resolve_model(
    registry: ModelRegistry,
    scenario: str,
    backend: str,
    runtime_type: str = "provider",
    manual_override: str | None = None,
) -> DistilledModelRecord | None:
    """Resolve the active model for a scenario/backend/runtime combination.

    Priority: manual override → active registry entry → None.
    """
    if manual_override:
        return DistilledModelRecord(
            artifact_id=manual_override,
            scenario=scenario,
            scenario_family="",
            backend=backend,
            checkpoint_path=manual_override,
            runtime_types=[runtime_type],
            activation_state="active",
            training_metrics={},
            provenance={"source": "manual_override"},
        )

    for rec in registry.list_for_scenario(scenario):
        if (
            rec.backend == backend
            and rec.activation_state == "active"
            and (not rec.runtime_types or runtime_type in rec.runtime_types)
        ):
            return rec

    return None


def publish_training_output(
    completion: TrainingCompletionOutput,
    registry: ModelRegistry,
    *,
    artifacts_root: Path | None = None,
    auto_activate: bool = False,
) -> DistilledModelRecord:
    """Publish a training output as a registered model artifact.

    Idempotent: re-publishing the same completion returns the same record.
    """
    from autocontext.artifacts import ArtifactProvenance
    from autocontext.artifacts import DistilledModelArtifact as PublishedDistilledModelArtifact

    artifact_id = _deterministic_artifact_id(completion)

    published_artifact = PublishedDistilledModelArtifact(
        id=artifact_id,
        name=f"{completion.scenario}-{completion.backend}-distilled",
        version=1,
        scenario=completion.scenario,
        compatible_scenarios=[completion.scenario],
        tags=[completion.backend, *( [completion.scenario_family] if completion.scenario_family else [] )],
        provenance=ArtifactProvenance(
            run_id=completion.run_id,
            generation=0,
            scenario=completion.scenario,
            settings={
                "backend": completion.backend,
                "runtime_types": list(completion.runtime_types),
            },
        ),
        architecture=completion.architecture or "autoresearch_gpt",
        parameter_count=max(int(completion.parameter_count), 1),
        checkpoint_path=completion.checkpoint_path,
        training_data_stats={
            **dict(completion.data_stats),
            "training_metrics": dict(completion.training_metrics),
            "metadata": dict(completion.metadata),
        },
    )

    if artifacts_root is not None:
        artifacts_dir = _artifact_dir(artifacts_root)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        _artifact_path(artifacts_root, artifact_id).write_text(
            published_artifact.model_dump_json(indent=2),
            encoding="utf-8",
        )

    existing = registry.load(artifact_id)
    if existing is not None:
        if auto_activate and existing.activation_state != "active":
            registry.activate(artifact_id)
            existing = registry.load(artifact_id) or existing
        return existing

    record = DistilledModelRecord(
        artifact_id=artifact_id,
        scenario=completion.scenario,
        scenario_family=completion.scenario_family,
        backend=completion.backend,
        checkpoint_path=completion.checkpoint_path,
        runtime_types=list(completion.runtime_types),
        activation_state="candidate",
        training_metrics=dict(completion.training_metrics),
        provenance={
            "run_id": completion.run_id,
            "parameter_count": completion.parameter_count,
            "architecture": completion.architecture,
            "data_stats": dict(completion.data_stats),
        },
        metadata={
            **dict(completion.metadata),
            **(
                {"published_artifact_path": str(_artifact_path(artifacts_root, artifact_id))}
                if artifacts_root is not None
                else {}
            ),
        },
    )

    registry.register(record)

    if auto_activate:
        registry.activate(artifact_id)
        record = registry.load(artifact_id) or record

    return record
