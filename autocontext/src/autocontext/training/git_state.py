"""Git-backed candidate and decision-history state for training runs."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TrainingGitState:
    """Own the training workspace's Git operations and checkpoint boundary."""

    work_dir: Path
    scenario: str
    checkpoint_root: Path

    def initialize(self) -> None:
        """Initialize the workspace repository and training branch."""

        git_dir = self.work_dir / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=self.work_dir, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "autocontext-train@local"],
                cwd=self.work_dir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "autocontext Training"],
                cwd=self.work_dir,
                capture_output=True,
                check=True,
            )

        self.stage_workspace_changes()
        subprocess.run(
            ["git", "commit", "-m", f"autocontext-train: setup workspace for {self.scenario}"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )
        branch_name = f"autocontext-train/{self.scenario}/{time.strftime('%Y%m%d-%H%M%S')}"
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def commit_candidate(self, message: str) -> None:
        """Commit candidate source changes without checkpoint payloads."""

        self.stage_workspace_changes()
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def stage_workspace_changes(self) -> None:
        """Stage workspace state while keeping durable checkpoints outside Git.

        If an accepted checkpoint is swept into the next candidate commit,
        ``reset --hard HEAD~1`` treats it as candidate-owned and deletes it.
        Excluding the backend tree also avoids copying large model payloads into
        the repository object database.
        """

        checkpoint_path = self.work_dir / self.checkpoint_root
        try:
            relative_checkpoint_root = checkpoint_path.resolve().relative_to(self.work_dir.resolve())
        except ValueError as exc:
            raise ValueError("backend checkpoint directory must be inside the training workspace") from exc
        if relative_checkpoint_root == Path("."):
            raise ValueError("backend checkpoint directory must not be the training workspace root")

        checkpoint_exclusion = f":(top,exclude,literal){relative_checkpoint_root.as_posix()}"
        subprocess.run(
            ["git", "add", "-A", "--", ".", checkpoint_exclusion],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def amend_decision(self, paths: list[Path]) -> None:
        """Attach an accepted decision record to its candidate commit."""

        if not (self.work_dir / ".git").exists():
            return
        self._stage_paths(paths)
        subprocess.run(
            ["git", "commit", "--amend", "--no-edit", "--allow-empty"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def commit_decision(self, paths: list[Path], *, experiment_index: int, outcome: str) -> None:
        """Commit non-accepted evidence after candidate code has been reset."""

        if not (self.work_dir / ".git").exists():
            return
        self._stage_paths(paths)
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"autocontext-train: record experiment {experiment_index} {outcome}",
                "--allow-empty",
            ],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def decision_record_paths(self, promotion_artifact_path: Path | None) -> list[Path]:
        """Return validated workspace-relative paths for a decision record."""

        paths = [self.work_dir / "results.tsv"]
        if promotion_artifact_path is not None and promotion_artifact_path.exists():
            paths.append(promotion_artifact_path)
        relative_paths: list[Path] = []
        for path in paths:
            try:
                relative_paths.append(path.resolve().relative_to(self.work_dir.resolve()))
            except ValueError as exc:
                raise ValueError("training decision artifacts must be inside the training workspace") from exc
        return relative_paths

    def head_sha(self) -> str:
        """Return the current commit SHA."""

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.work_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def discard_candidate(self, checkpoint_path: Path | None = None) -> None:
        """Reset candidate code and remove only its scoped checkpoint."""

        subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )
        if checkpoint_path is None or not checkpoint_path.exists():
            return
        try:
            checkpoint_path.resolve().relative_to(self.work_dir.resolve())
        except ValueError as exc:
            raise ValueError("checkpoint to discard must be inside the training workspace") from exc
        shutil.rmtree(checkpoint_path)

    def _stage_paths(self, paths: list[Path]) -> None:
        subprocess.run(
            ["git", "add", "--", *(str(path) for path in paths)],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )


__all__ = ["TrainingGitState"]
