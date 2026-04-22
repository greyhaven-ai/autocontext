"""Evidence workspace domain model (AC-504)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EvidenceArtifact:
    """A single piece of evidence from a prior run."""

    artifact_id: str
    source_run_id: str
    kind: str  # "trace", "role_output", "report", "tool", "gate_decision", "log"
    path: str  # relative path within workspace
    summary: str  # one-line description
    size_bytes: int
    generation: int | None
    source_path: str = ""
    source_mtime_ns: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "source_run_id": self.source_run_id,
            "kind": self.kind,
            "path": self.path,
            "summary": self.summary,
            "size_bytes": self.size_bytes,
            "generation": self.generation,
            "source_path": self.source_path,
            "source_mtime_ns": self.source_mtime_ns,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceArtifact:
        return cls(
            artifact_id=data["artifact_id"],
            source_run_id=data["source_run_id"],
            kind=data["kind"],
            path=data["path"],
            summary=data["summary"],
            size_bytes=data["size_bytes"],
            generation=data.get("generation"),
            source_path=str(data.get("source_path", "")),
            source_mtime_ns=data.get("source_mtime_ns"),
        )


@dataclass(slots=True)
class EvidenceWorkspace:
    """Materialized view of prior-run artifacts for optimizer roles."""

    workspace_dir: str
    source_runs: list[str]
    artifacts: list[EvidenceArtifact]
    total_size_bytes: int
    materialized_at: str
    source_signature: str = ""
    cache_hit: bool = False
    accessed_artifacts: list[str] = field(default_factory=list)

    def get_artifact(self, artifact_id: str) -> EvidenceArtifact | None:
        for a in self.artifacts:
            if a.artifact_id == artifact_id:
                return a
        return None

    def list_by_kind(self, kind: str) -> list[EvidenceArtifact]:
        return [a for a in self.artifacts if a.kind == kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_dir": self.workspace_dir,
            "source_runs": list(self.source_runs),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "total_size_bytes": self.total_size_bytes,
            "materialized_at": self.materialized_at,
            "source_signature": self.source_signature,
            "cache_hit": self.cache_hit,
            "accessed_artifacts": list(self.accessed_artifacts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceWorkspace:
        return cls(
            workspace_dir=data["workspace_dir"],
            source_runs=data.get("source_runs", []),
            artifacts=[EvidenceArtifact.from_dict(a) for a in data.get("artifacts", [])],
            total_size_bytes=data.get("total_size_bytes", 0),
            materialized_at=data["materialized_at"],
            source_signature=str(data.get("source_signature", "")),
            cache_hit=bool(data.get("cache_hit", False)),
            accessed_artifacts=data.get("accessed_artifacts", []),
        )
