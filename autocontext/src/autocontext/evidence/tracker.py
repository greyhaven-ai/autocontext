"""Evidence access tracking (AC-504)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from autocontext.evidence.workspace import EvidenceWorkspace

_ACCESS_LOG_FILENAME = "evidence_access_log.json"


def record_access(workspace: EvidenceWorkspace, artifact_id: str) -> None:
    """Record that an artifact was consulted. Deduplicates."""
    if artifact_id not in workspace.accessed_artifacts:
        workspace.accessed_artifacts.append(artifact_id)


def save_access_log(workspace: EvidenceWorkspace) -> None:
    """Persist the access log as JSON alongside the workspace."""
    log_path = Path(workspace.workspace_dir) / _ACCESS_LOG_FILENAME
    log_path.write_text(
        json.dumps({"accessed": workspace.accessed_artifacts}, indent=2),
        encoding="utf-8",
    )


def load_access_log(workspace_dir: str) -> list[str]:
    """Load the access log from a workspace directory."""
    log_path = Path(workspace_dir) / _ACCESS_LOG_FILENAME
    if not log_path.exists():
        return []
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        accessed = data.get("accessed", [])
        if not isinstance(accessed, list):
            return []
        return [artifact_id for artifact_id in accessed if isinstance(artifact_id, str)]
    except (json.JSONDecodeError, OSError):
        return []


def compute_utilization(workspace: EvidenceWorkspace) -> dict[str, Any]:
    """Return utilization stats."""
    total = len(workspace.artifacts)
    accessed = len(workspace.accessed_artifacts)
    pct = round(accessed / total * 100, 1) if total > 0 else 0.0

    kind_counts: Counter[str] = Counter(a.kind for a in workspace.artifacts)
    accessed_set = set(workspace.accessed_artifacts)
    kind_accessed: Counter[str] = Counter(a.kind for a in workspace.artifacts if a.artifact_id in accessed_set)
    by_kind: dict[str, dict[str, int]] = {}
    for kind in kind_counts:
        by_kind[kind] = {
            "total": kind_counts[kind],
            "accessed": kind_accessed.get(kind, 0),
        }

    return {
        "total_artifacts": total,
        "accessed_count": accessed,
        "utilization_percent": pct,
        "by_kind": by_kind,
    }
