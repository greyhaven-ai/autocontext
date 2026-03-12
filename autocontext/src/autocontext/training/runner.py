"""Training loop runner with git experiment state machine (AC-179).

Orchestrates the autoresearch-style experiment loop:
1. Set up workspace (copy templates, create branch, init results.tsv)
2. Render program.md with scenario-specific context
3. Run experiments in a keep/discard git state machine
4. Return path to best checkpoint
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

CONVERGENCE_NUDGE_THRESHOLD = 10
_TEMPLATE_DIR = Path(__file__).parent / "autoresearch"

_TSV_HEADER = "experiment\tavg_score\tvalid_rate\tpeak_memory_mb\ttraining_seconds\toutcome\terror\n"


class ExperimentOutcome(StrEnum):
    KEPT = "kept"
    DISCARDED = "discarded"
    ERROR = "error"


@dataclass(slots=True)
class TrainingConfig:
    """Configuration for the autoresearch training loop."""

    scenario: str
    data_path: Path
    time_budget: int = 300
    max_experiments: int = 0
    memory_limit_mb: int = 16384
    agent_provider: str = "anthropic"
    agent_model: str = "claude-sonnet-4-20250514"


@dataclass(slots=True)
class ExperimentResult:
    """Result of a single training experiment."""

    experiment_index: int
    avg_score: float
    valid_rate: float
    peak_memory_mb: float
    training_seconds: float
    outcome: ExperimentOutcome
    error_message: str = ""


@dataclass(slots=True)
class TrainingResult:
    """Final result of a training session."""

    scenario: str
    total_experiments: int
    kept_count: int
    discarded_count: int
    best_score: float
    best_experiment_index: int
    checkpoint_path: Path | None
    results: list[ExperimentResult] = field(default_factory=list)

    @property
    def kept_ratio(self) -> float:
        if self.total_experiments == 0:
            return 0.0
        return self.kept_count / self.total_experiments


class TrainingRunner:
    """Manages the autoresearch experiment loop with git state machine."""

    def __init__(self, config: TrainingConfig, *, work_dir: Path) -> None:
        self.config = config
        self.work_dir = work_dir
        self._best_score = 0.0
        self._best_experiment_index = -1

    @property
    def subprocess_timeout(self) -> int:
        """Wall-clock timeout for experiment subprocesses (2x time budget)."""
        return self.config.time_budget * 2

    def setup_workspace(self) -> None:
        """Copy template files, create git branch, render program.md, init results.tsv."""
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Copy template files
        for filename in ("train.py", "prepare.py"):
            src = _TEMPLATE_DIR / filename
            if src.exists():
                shutil.copy2(src, self.work_dir / filename)

        # Render program.md with scenario context
        from autocontext.training.autoresearch.program import render_program

        rendered = render_program(
            scenario=self.config.scenario,
            strategy_schema="(see scenario definition)",
            playbook_summary="(no playbook loaded)",
            dead_ends_summary="(none known)",
            time_budget=str(self.config.time_budget),
            memory_limit=str(self.config.memory_limit_mb),
        )
        (self.work_dir / "program.md").write_text(rendered, encoding="utf-8")

        # Initialize results.tsv
        (self.work_dir / "results.tsv").write_text(_TSV_HEADER, encoding="utf-8")

        # Create git branch if in a git repo
        self._try_create_branch()

    def _try_create_branch(self) -> None:
        """Initialize a git repo in the workspace and create a training branch.

        Failures are silently ignored — git tracking is optional.
        """
        try:
            self._init_git_repo()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return

    def _init_git_repo(self) -> None:
        """Initialize git repo, commit workspace files, and create training branch."""
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
                ["git", "config", "user.name", "AutoContext Training"],
                cwd=self.work_dir,
                capture_output=True,
                check=True,
            )

        subprocess.run(["git", "add", "-A"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"autocontext-train: setup workspace for {self.config.scenario}"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        branch_name = f"autocontext-train/{self.config.scenario}/{timestamp}"
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def _git_commit(self, message: str) -> None:
        """Stage all changes and create a commit."""
        subprocess.run(["git", "add", "-A"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def _git_head_sha(self) -> str:
        """Return current HEAD commit SHA."""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.work_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def keep_experiment(self) -> None:
        """Keep the current experiment (HEAD stays as-is)."""
        # Nothing to do — the commit is already at HEAD

    def discard_experiment(self) -> None:
        """Discard the current experiment by resetting HEAD~1."""
        subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def record_result(self, result: ExperimentResult) -> None:
        """Append an experiment result to results.tsv."""
        tsv_path = self.work_dir / "results.tsv"
        line = (
            f"{result.experiment_index}\t"
            f"{result.avg_score}\t"
            f"{result.valid_rate}\t"
            f"{result.peak_memory_mb}\t"
            f"{result.training_seconds}\t"
            f"{result.outcome.value}\t"
            f"{result.error_message}\n"
        )
        with open(tsv_path, "a", encoding="utf-8") as f:
            f.write(line)

    def should_stop(self, *, experiment_count: int) -> bool:
        """Check if the training loop should stop."""
        if self.config.max_experiments > 0 and experiment_count >= self.config.max_experiments:
            return True
        return False

    def needs_convergence_nudge(self, *, consecutive_discards: int) -> bool:
        """Check if the agent needs a convergence nudge."""
        return consecutive_discards >= CONVERGENCE_NUDGE_THRESHOLD

    def parse_summary(self, stdout: str) -> dict[str, float] | None:
        """Parse the training summary block from subprocess stdout.

        Returns a dict with avg_score, valid_rate, peak_memory_mb, training_seconds,
        or None if the summary block is not found.
        """
        match = re.search(
            r"=== TRAINING SUMMARY ===\n(.*?)\n========================",
            stdout,
            re.DOTALL,
        )
        if not match:
            return None

        block = match.group(1)
        result: dict[str, float] = {}
        for line in block.strip().split("\n"):
            line = line.strip()
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                try:
                    result[key] = float(val.strip())
                except ValueError:
                    pass

        required = {"avg_score", "valid_rate", "peak_memory_mb", "training_seconds"}
        if not required.issubset(result.keys()):
            return None
        return result

    def build_training_result(self, results: list[ExperimentResult]) -> TrainingResult:
        """Build the final TrainingResult from accumulated experiment results."""
        kept = [r for r in results if r.outcome == ExperimentOutcome.KEPT]
        discarded = [r for r in results if r.outcome == ExperimentOutcome.DISCARDED]

        best_score = 0.0
        best_index = -1
        for r in kept:
            if r.avg_score > best_score:
                best_score = r.avg_score
                best_index = r.experiment_index

        return TrainingResult(
            scenario=self.config.scenario,
            total_experiments=len(results),
            kept_count=len(kept),
            discarded_count=len(discarded),
            best_score=best_score,
            best_experiment_index=best_index,
            checkpoint_path=self.work_dir if best_index >= 0 else None,
            results=results,
        )
