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
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VALID_STATES = frozenset({"candidate", "active", "disabled", "deprecated"})


@dataclass(slots=True)
class DistilledModelRecord:
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
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "scenario": self.scenario,
            "scenario_family": self.scenario_family,
            "backend": self.backend,
            "checkpoint_path": self.checkpoint_path,
            "runtime_types": self.runtime_types,
            "activation_state": self.activation_state,
            "training_metrics": self.training_metrics,
            "provenance": self.provenance,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DistilledModelRecord:
        return cls(
            artifact_id=data["artifact_id"],
            scenario=data.get("scenario", ""),
            scenario_family=data.get("scenario_family", ""),
            backend=data.get("backend", ""),
            checkpoint_path=data.get("checkpoint_path", ""),
            runtime_types=data.get("runtime_types", []),
            activation_state=data.get("activation_state", "candidate"),
            training_metrics=data.get("training_metrics", {}),
            provenance=data.get("provenance", {}),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class DistilledModelArtifact:
    """Published artifact with training and architecture metadata."""

    artifact_id: str
    checkpoint_path: str
    backend: str
    scenario: str
    parameter_count: int
    architecture: str
    training_metrics: dict[str, Any]
    data_stats: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "checkpoint_path": self.checkpoint_path,
            "backend": self.backend,
            "scenario": self.scenario,
            "parameter_count": self.parameter_count,
            "architecture": self.architecture,
            "training_metrics": self.training_metrics,
            "data_stats": self.data_stats,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DistilledModelArtifact:
        return cls(
            artifact_id=data["artifact_id"],
            checkpoint_path=data.get("checkpoint_path", ""),
            backend=data.get("backend", ""),
            scenario=data.get("scenario", ""),
            parameter_count=data.get("parameter_count", 0),
            architecture=data.get("architecture", ""),
            training_metrics=data.get("training_metrics", {}),
            data_stats=data.get("data_stats", {}),
            metadata=data.get("metadata", {}),
        )


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


class ModelRegistry:
    """JSON-file registry for distilled model artifacts."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "model_registry"
        self._dir.mkdir(parents=True, exist_ok=True)

    def register(self, record: DistilledModelRecord) -> Path:
        path = self._dir / f"{record.artifact_id}.json"
        path.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, artifact_id: str) -> DistilledModelRecord | None:
        path = self._dir / f"{artifact_id}.json"
        if not path.exists():
            return None
        return DistilledModelRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_all(self) -> list[DistilledModelRecord]:
        return [
            DistilledModelRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self._dir.glob("*.json"))
        ]

    def list_for_scenario(self, scenario: str) -> list[DistilledModelRecord]:
        return [r for r in self.list_all() if r.scenario == scenario]

    def activate(self, artifact_id: str) -> None:
        """Activate a model, deactivating any other active model for same scenario+backend."""
        target = self.load(artifact_id)
        if target is None:
            raise ValueError(f"Artifact {artifact_id} not found")

        # Deactivate previous active for same scenario+backend
        for rec in self.list_all():
            if (
                rec.artifact_id != artifact_id
                and rec.scenario == target.scenario
                and rec.backend == target.backend
                and rec.activation_state == "active"
            ):
                rec.activation_state = "disabled"  # type: ignore[misc]
                self.register(rec)

        target.activation_state = "active"  # type: ignore[misc]
        self.register(target)

    def deactivate(self, artifact_id: str) -> None:
        rec = self.load(artifact_id)
        if rec is None:
            raise ValueError(f"Artifact {artifact_id} not found")
        rec.activation_state = "disabled"  # type: ignore[misc]
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
    auto_activate: bool = False,
) -> DistilledModelRecord:
    """Publish a training output as a registered model artifact.

    Idempotent: re-publishing the same completion returns the same record.
    """
    artifact_id = _deterministic_artifact_id(completion)

    existing = registry.load(artifact_id)
    if existing is not None:
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
        metadata=dict(completion.metadata),
    )

    registry.register(record)

    if auto_activate:
        registry.activate(artifact_id)
        record.activation_state = "active"  # type: ignore[misc]

    return record
