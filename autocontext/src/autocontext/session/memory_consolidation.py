"""Background memory consolidation for long-running sessions (AC-516).

Domain concepts:
- ConsolidationTrigger: decides when enough work has accumulated
- ConsolidationResult: structured audit of what was reviewed/promoted
- ConsolidationLock: file-based concurrency guard
- MemoryConsolidator: workflow orchestrator
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autocontext.knowledge.compaction import extract_promotable_lines

logger = logging.getLogger(__name__)


# ---- Value Objects ----


class ConsolidationTrigger(BaseModel):
    """Decides whether consolidation is warranted.

    Runs when completed_turns OR completed_sessions exceeds thresholds,
    or when force=True.
    """

    min_completed_turns: int = Field(default=10, ge=0)
    min_completed_sessions: int = Field(default=1, ge=0)

    def should_run(
        self,
        completed_turns: int,
        completed_sessions: int,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        return (
            completed_turns >= self.min_completed_turns
            or completed_sessions >= self.min_completed_sessions
        )

    model_config = {"frozen": True}


class ConsolidationResult(BaseModel):
    """Structured audit of a consolidation pass."""

    reviewed_sessions: list[str] = Field(default_factory=list)
    promoted_lessons: list[str] = Field(default_factory=list)
    promoted_hints: list[str] = Field(default_factory=list)
    promoted_notebook_updates: list[str] = Field(default_factory=list)
    skipped_reason: str = ""
    dry_run: bool = False

    @property
    def total_promoted(self) -> int:
        return (
            len(self.promoted_lessons)
            + len(self.promoted_hints)
            + len(self.promoted_notebook_updates)
        )

    @property
    def was_productive(self) -> bool:
        return self.total_promoted > 0

    model_config = {"frozen": True}


# ---- Concurrency Guard ----


class ConsolidationLock:
    """File-based lock preventing duplicate concurrent consolidation.

    Usable as a context manager:
        with ConsolidationLock(path):
            # do consolidation
    """

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._held = False

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns False if already held."""
        if self._held:
            return False
        try:
            with self._path.open("x", encoding="utf-8") as handle:
                handle.write("locked")
        except FileExistsError:
            return False
        self._held = True
        return True

    def acquire_or_raise(self) -> None:
        """Acquire or raise RuntimeError."""
        if not self.acquire():
            msg = "Consolidation is already running"
            raise RuntimeError(msg)

    def release(self) -> None:
        """Release the lock."""
        if not self._held:
            return
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        self._held = False

    def __enter__(self) -> ConsolidationLock:
        self.acquire_or_raise()
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


# ---- Workflow Orchestrator ----


class MemoryConsolidator:
    """Orchestrates the consolidation workflow.

    Reviews completed work artifacts and promotes durable learnings
    into memory surfaces (lessons, hints, notebook).
    """

    def __init__(self, trigger: ConsolidationTrigger | None = None) -> None:
        self._trigger = trigger or ConsolidationTrigger()

    def run(
        self,
        completed_turns: int,
        completed_sessions: int,
        artifacts: dict[str, Any],
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> ConsolidationResult:
        """Execute a consolidation pass.

        Returns a structured result describing what was reviewed and promoted.
        """
        if not self._trigger.should_run(completed_turns, completed_sessions, force=force):
            return ConsolidationResult(skipped_reason="threshold not met")

        # Review artifacts and extract promotable learnings
        promoted_lessons: list[str] = []
        promoted_hints: list[str] = []
        promoted_notebook: list[str] = []
        reviewed: list[str] = []

        # Extract from session reports
        session_reports = artifacts.get("session_reports", [])
        if isinstance(session_reports, list):
            for report in session_reports:
                if isinstance(report, str) and len(report.strip()) > 20:
                    promoted_lessons.extend(extract_promotable_lines(report, max_items=3))
                    reviewed.append("session_report")

        # Extract from verification outcomes
        verifications = artifacts.get("verification_outcomes", [])
        if isinstance(verifications, list):
            for v in verifications:
                if isinstance(v, dict) and v.get("passed") and v.get("reason"):
                    promoted_hints.append(str(v["reason"])[:200])

        # Extract from notebook state
        notebook = artifacts.get("notebook_state", {})
        if isinstance(notebook, dict):
            objective = notebook.get("current_objective", "")
            if objective:
                promoted_notebook.append(f"objective: {objective}")

        if dry_run:
            return ConsolidationResult(
                reviewed_sessions=reviewed,
                promoted_lessons=promoted_lessons,
                promoted_hints=promoted_hints,
                promoted_notebook_updates=promoted_notebook,
                dry_run=True,
            )

        # In non-dry-run mode, actual persistence would happen here
        # (writing to lesson store, hint manager, notebook, etc.)
        # For now we return what would be promoted.
        logger.info(
            "consolidation: %d lessons, %d hints, %d notebook updates",
            len(promoted_lessons), len(promoted_hints), len(promoted_notebook),
        )

        return ConsolidationResult(
            reviewed_sessions=reviewed,
            promoted_lessons=promoted_lessons,
            promoted_hints=promoted_hints,
            promoted_notebook_updates=promoted_notebook,
        )
