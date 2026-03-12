"""Tests that restore_knowledge_snapshot uses versioned playbook writes."""
from __future__ import annotations

from pathlib import Path

from autocontext.storage.artifacts import ArtifactStore


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        max_playbook_versions=5,
    )


def test_restore_archives_existing_playbook(tmp_path: Path) -> None:
    """Restoring a snapshot should archive the current playbook via write_playbook."""
    store = _make_store(tmp_path)
    scenario = "grid_ctf"

    # Write initial playbook
    store.write_playbook(scenario, "Current playbook content")

    # Create a fake snapshot
    snapshot_dir = tmp_path / "knowledge" / scenario / "snapshots" / "old_run"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "playbook.md").write_text("Restored playbook content", encoding="utf-8")

    # Restore — this should archive "Current playbook content"
    result = store.restore_knowledge_snapshot(scenario, "old_run")
    assert result is True

    # The restored content should be current
    current = store.read_playbook(scenario)
    assert "Restored playbook content" in current

    # The previous playbook should have been archived
    versions_dir = tmp_path / "knowledge" / scenario / "playbook_versions"
    assert versions_dir.exists(), "Expected versioning to archive the previous playbook"
    versions = list(versions_dir.glob("playbook_v*.md"))
    assert len(versions) == 1, f"Expected 1 archived version, found {len(versions)}"
    archived = versions[0].read_text(encoding="utf-8")
    assert "Current playbook content" in archived
