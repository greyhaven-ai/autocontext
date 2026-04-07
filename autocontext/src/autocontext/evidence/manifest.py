"""Evidence workspace prompt rendering (AC-504)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from autocontext.evidence.workspace import EvidenceArtifact, EvidenceWorkspace


def render_evidence_manifest(workspace: EvidenceWorkspace) -> str:
    """Render a compact prompt section describing available evidence."""
    n = len(workspace.artifacts)
    runs = len(workspace.source_runs)
    size_mb = round(workspace.total_size_bytes / (1024 * 1024), 1)

    lines = [
        "## Prior-Run Evidence",
        f"Available: {n} artifacts from {runs} prior run(s) ({size_mb} MB)",
    ]

    kind_counts: Counter[str] = Counter(a.kind for a in workspace.artifacts)
    kind_labels = {
        "gate_decision": "Gate decisions (advance/retry/rollback with deltas)",
        "trace": "Traces (run event streams)",
        "report": "Reports (session + weakness reports)",
        "role_output": "Role outputs (analyst, architect, coach)",
        "tool": "Tools (architect-generated)",
        "log": "Logs (execution logs)",
    }
    for kind in ["gate_decision", "trace", "report", "role_output", "tool", "log"]:
        count = kind_counts.get(kind, 0)
        if count > 0:
            label = kind_labels.get(kind, kind)
            lines.append(f"- {label}: {count}")

    lines.append("")
    lines.append('Reference artifacts by ID (e.g., "gate_abc123") for detailed inspection.')

    return "\n".join(lines)


def render_artifact_detail(artifact: EvidenceArtifact, workspace_dir: str) -> str:
    """Read and return the content of a specific artifact."""
    path = Path(workspace_dir) / artifact.path
    if not path.exists():
        return f"[Artifact {artifact.artifact_id} not found at {artifact.path}]"
    try:
        content = path.read_text(encoding="utf-8")
        return f"## {artifact.kind}: {artifact.summary}\n\n{content}"
    except (OSError, UnicodeDecodeError):
        return f"[Could not read artifact {artifact.artifact_id}: binary or inaccessible]"
