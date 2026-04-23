"""Evidence workspace prompt rendering (AC-504)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from autocontext.evidence.workspace import EvidenceArtifact, EvidenceWorkspace


def render_evidence_manifest(workspace: EvidenceWorkspace, *, role: str = "default") -> str:
    """Render a compact prompt section describing available evidence."""
    n = len(workspace.artifacts)
    runs = len(workspace.source_runs)
    size_mb = round(workspace.total_size_bytes / (1024 * 1024), 1)
    role_suffix = f" ({role.title()})" if role != "default" else ""

    lines = [
        f"## Prior-Run Evidence{role_suffix}",
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

    cards = _top_evidence_cards(workspace, role=role)
    if cards:
        lines.append("")
        lines.append("Top evidence cards:")
        for artifact in cards:
            path_label = Path(artifact.path).name
            generation_label = f"gen {artifact.generation}" if artifact.generation is not None else "gen n/a"
            lines.append(
                f"- {artifact.artifact_id} | {artifact.kind} | {artifact.source_run_id} | {generation_label} | {path_label}"
            )
            lines.append(f"  Summary: {artifact.summary}")

    lines.append("")
    lines.append('Reference artifacts by ID (e.g., "gate_abc123") for detailed inspection.')

    return "\n".join(lines)


def render_artifact_detail(
    artifact: EvidenceArtifact,
    workspace_dir: str,
    *,
    excerpt_lines: int | None = None,
) -> str:
    """Read and return the content of a specific artifact."""
    path = _resolve_workspace_path(Path(workspace_dir), artifact.path)
    if path is None or not path.exists():
        return f"[Artifact {artifact.artifact_id} not found at {artifact.path}]"
    try:
        content = path.read_text(encoding="utf-8")
        if excerpt_lines is not None and excerpt_lines > 0:
            content = _excerpt_content(content, excerpt_lines=excerpt_lines)
        source_path = artifact.source_path or artifact.path
        return (
            f"## {artifact.kind}: {artifact.summary}\n\n"
            f"Artifact ID: {artifact.artifact_id}\n"
            f"Source run: {artifact.source_run_id}\n"
            f"Source path: {source_path}\n\n"
            f"{content}"
        )
    except (OSError, UnicodeDecodeError):
        return f"[Could not read artifact {artifact.artifact_id}: binary or inaccessible]"


def _resolve_workspace_path(workspace_dir: Path, rel_path: str) -> Path | None:
    """Resolve a manifest path inside the workspace and reject directory escapes."""
    candidate = (workspace_dir / rel_path).resolve()
    try:
        candidate.relative_to(workspace_dir.resolve())
    except ValueError:
        return None
    return candidate


def _excerpt_content(content: str, *, excerpt_lines: int) -> str:
    lines = content.splitlines()
    if len(lines) <= excerpt_lines:
        return content
    excerpt = "\n".join(lines[:excerpt_lines]).rstrip()
    omitted = len(lines) - excerpt_lines
    return (
        f"{excerpt}\n"
        f"[... {omitted} additional lines omitted; request full artifact for complete content ...]"
    )


def _top_evidence_cards(workspace: EvidenceWorkspace, *, role: str) -> list[EvidenceArtifact]:
    weights_by_role: dict[str, dict[str, int]] = {
        "analyst": {
            "gate_decision": 5,
            "report": 4,
            "role_output": 3,
            "trace": 3,
            "log": 2,
            "tool": 1,
        },
        "architect": {
            "tool": 5,
            "gate_decision": 4,
            "trace": 3,
            "role_output": 3,
            "report": 2,
            "log": 2,
        },
    }
    weights = weights_by_role.get(role, {})
    ranked = sorted(
        workspace.artifacts,
        key=lambda artifact: (
            weights.get(artifact.kind, 2),
            artifact.generation if artifact.generation is not None else -1,
            artifact.size_bytes,
        ),
        reverse=True,
    )
    return ranked[:5]
