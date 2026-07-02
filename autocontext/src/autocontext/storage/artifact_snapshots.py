"""Cross-run knowledge snapshot methods for ArtifactStore."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Protocol

from autocontext.util.json_io import read_json


class SnapshotHost(Protocol):
    def _scenario_dir(self, scenario_name: str) -> Path: ...
    def _skill_dir(self, scenario_name: str) -> Path: ...
    def _hint_state_path(self, scenario_name: str) -> Path: ...
    def harness_dir(self, scenario_name: str) -> Path: ...
    def write_playbook(self, scenario_name: str, content: str) -> None: ...
    def write_markdown(self, path: Path, content: str) -> None: ...
    def write_json(self, path: Path, payload: dict[str, Any]) -> None: ...


class SnapshotMethods:
    def snapshot_knowledge(self: SnapshotHost, scenario_name: str, run_id: str) -> str:
        """Copy playbook + skills + hints to snapshots/<run_id>/. Returns playbook hash."""
        scenario_dir = self._scenario_dir(scenario_name)
        snapshot_dir = scenario_dir / "snapshots" / run_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        playbook_content = ""
        playbook_path = scenario_dir / "playbook.md"
        if playbook_path.exists():
            playbook_content = playbook_path.read_text(encoding="utf-8")
            (snapshot_dir / "playbook.md").write_text(playbook_content, encoding="utf-8")

        hints_path = scenario_dir / "hints.md"
        if hints_path.exists():
            (snapshot_dir / "hints.md").write_text(hints_path.read_text(encoding="utf-8"), encoding="utf-8")
        hint_state_path = self._hint_state_path(scenario_name)
        if hint_state_path.exists():
            (snapshot_dir / "hint_state.json").write_text(
                hint_state_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

        skill_dir = self._skill_dir(scenario_name)
        skill_path = skill_dir / "SKILL.md"
        if skill_path.exists():
            (snapshot_dir / "SKILL.md").write_text(skill_path.read_text(encoding="utf-8"), encoding="utf-8")

        # Snapshot harness files
        h_dir = self.harness_dir(scenario_name)
        if h_dir.exists():
            harness_snapshot = snapshot_dir / "harness"
            harness_snapshot.mkdir(parents=True, exist_ok=True)
            for py_file in h_dir.glob("*.py"):
                if py_file.is_file():
                    (harness_snapshot / py_file.name).write_text(
                        py_file.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )

        return hashlib.sha256(playbook_content.encode("utf-8")).hexdigest()[:16]

    def restore_knowledge_snapshot(self: SnapshotHost, scenario_name: str, source_run_id: str) -> bool:
        """Restore knowledge from a snapshot. Returns True if restored."""
        scenario_dir = self._scenario_dir(scenario_name)
        snapshot_dir = scenario_dir / "snapshots" / source_run_id
        if not snapshot_dir.exists():
            return False

        restored = False
        pb_snapshot = snapshot_dir / "playbook.md"
        if pb_snapshot.exists():
            self.write_playbook(scenario_name, pb_snapshot.read_text(encoding="utf-8"))
            restored = True

        hints_snapshot = snapshot_dir / "hints.md"
        if hints_snapshot.exists():
            self.write_markdown(
                scenario_dir / "hints.md",
                hints_snapshot.read_text(encoding="utf-8"),
            )
            restored = True
        hint_state_snapshot = snapshot_dir / "hint_state.json"
        if hint_state_snapshot.exists():
            self.write_json(
                self._hint_state_path(scenario_name),
                read_json(hint_state_snapshot),
            )
            restored = True

        skill_snapshot = snapshot_dir / "SKILL.md"
        if skill_snapshot.exists():
            skill_dir = self._skill_dir(scenario_name)
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(skill_snapshot.read_text(encoding="utf-8"), encoding="utf-8")
            restored = True

        # Restore harness files from snapshot
        harness_snapshot = snapshot_dir / "harness"
        if harness_snapshot.exists():
            h_dir = self.harness_dir(scenario_name)
            h_dir.mkdir(parents=True, exist_ok=True)
            for py_file in harness_snapshot.glob("*.py"):
                if py_file.is_file():
                    (h_dir / py_file.name).write_text(
                        py_file.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
            restored = True

        return restored
