"""Session artifact collector (AC-519).

Finds and packages source artifacts for sharing from a run directory
and optional knowledge directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SessionArtifact:
    """A source artifact eligible for sharing."""

    name: str
    path: Path
    size_bytes: int
    category: str  # "trace", "session", "report", "playbook", "output"


# Files worth including in a share bundle, by category
_RUN_FILE_CATEGORIES: dict[str, str] = {
    "pi_session.json": "session",
    "pi_output.txt": "output",
    "events.ndjson": "trace",
    "session_report.md": "report",
}

_KNOWLEDGE_FILE_CATEGORIES: dict[str, str] = {
    "playbook.md": "playbook",
    "dead_ends.md": "report",
}


def collect_session_artifacts(
    runs_root: Path,
    knowledge_root: Path,
    run_id: str,
    scenario_name: str | None = None,
) -> list[SessionArtifact]:
    """Collect shareable artifacts from a run and optional knowledge directory."""
    artifacts: list[SessionArtifact] = []

    run_dir = runs_root / run_id
    if run_dir.is_dir():
        artifacts.extend(_scan_run_dir(run_dir))

    if scenario_name:
        k_dir = knowledge_root / scenario_name
        if k_dir.is_dir():
            artifacts.extend(_scan_knowledge_dir(k_dir))

    return artifacts


def _scan_run_dir(run_dir: Path) -> list[SessionArtifact]:
    """Scan a run directory for shareable files."""
    artifacts: list[SessionArtifact] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        category = _RUN_FILE_CATEGORIES.get(path.name)
        if category is None:
            # Check for generation-level outputs
            if path.name.endswith("_output.md") or path.name.endswith("_output.txt"):
                category = "output"
            elif path.name.endswith(".ndjson"):
                category = "trace"
            elif path.name.endswith("_report.md"):
                category = "report"
            else:
                continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        artifacts.append(SessionArtifact(name=path.name, path=path, size_bytes=size, category=category))
    return artifacts


def _scan_knowledge_dir(k_dir: Path) -> list[SessionArtifact]:
    """Scan knowledge directory for shareable files."""
    artifacts: list[SessionArtifact] = []
    for fname, category in _KNOWLEDGE_FILE_CATEGORIES.items():
        path = k_dir / fname
        if path.is_file():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            artifacts.append(SessionArtifact(name=fname, path=path, size_bytes=size, category=category))
    return artifacts
