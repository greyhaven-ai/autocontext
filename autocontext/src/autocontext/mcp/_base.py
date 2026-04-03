"""MCP shared types and helpers."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from autocontext.config import AppSettings
from autocontext.execution.verification_dataset import (
    DatasetRegistry,
    resolve_objective_verification_config,
)
from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder
from autocontext.storage import ArtifactStore, SQLiteStore

logger = logging.getLogger(__name__)

_OPENCLAW_VERSION = "0.1.0"
_TASK_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")


class MtsToolContext:
    """Lazy-initialized shared state for MCP tool implementations."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.sqlite = SQLiteStore(settings.db_path)
        migrations_dir = Path(__file__).resolve().parents[3] / "migrations"
        self.sqlite.migrate(migrations_dir)
        self.artifacts = ArtifactStore(
            settings.runs_root,
            settings.knowledge_root,
            settings.skills_root,
            settings.claude_skills_path,
            max_playbook_versions=settings.playbook_max_versions,
        )
        self.trajectory = ScoreTrajectoryBuilder(self.sqlite)


def _resolve_objective_verification(
    ctx: MtsToolContext,
    objective_verification: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Resolve inline or dataset-backed objective verification into live config."""
    if objective_verification is None:
        return None
    config, _dataset = resolve_objective_verification_config(
        objective_verification,
        DatasetRegistry(ctx.settings.knowledge_root),
    )
    if config is None:
        return None
    resolved = config.to_dict()
    guardrail = objective_verification.get("guardrail")
    if isinstance(guardrail, dict):
        resolved["guardrail"] = guardrail
    return resolved


def _validate_task_name(name: str) -> str | None:
    """Return an error message if the task name is invalid, else None."""
    if not name or not _TASK_NAME_RE.match(name):
        return "Invalid task name: must be 1-128 alphanumeric chars, hyphens, or underscores"
    return None
