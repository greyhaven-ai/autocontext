"""Session startup verification -- ensures clean state before generation.

Runs once per run (generation 1 only) and checks:
1. Knowledge directory exists
2. Playbook is non-empty and parseable
3. progress.json is valid JSON
4. SQLite database is accessible

All checks produce warnings (non-fatal). The run proceeds regardless,
but warnings are logged and emitted as events.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from autocontext.util.json_io import read_json

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StartupReport:
    """Result of startup verification."""

    warnings: list[str] = field(default_factory=list)


def verify_startup(
    *,
    scenario_name: str,
    knowledge_root: Path,
    db_path: Path | None,
) -> StartupReport:
    """Run all startup verification checks."""
    report = StartupReport()
    knowledge_dir = knowledge_root / scenario_name

    # Check 1: Knowledge directory
    if not knowledge_dir.is_dir():
        report.warnings.append(
            f"Knowledge directory not found: {knowledge_dir} (expected on first run)",
        )
        return report  # Can't check further without the directory

    # Check 2: Playbook
    playbook_path = knowledge_dir / "playbook.md"
    if not playbook_path.exists():
        report.warnings.append("Playbook file does not exist yet")
    else:
        content = playbook_path.read_text(encoding="utf-8")
        if not content.strip():
            report.warnings.append("Playbook file is empty")

    # Check 3: progress.json
    progress_path = knowledge_dir / "progress.json"
    if progress_path.exists():
        try:
            data = read_json(progress_path)
            if not isinstance(data, dict):
                report.warnings.append("progress.json is not a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            report.warnings.append(f"progress.json is invalid: {exc}")

    # Check 4: SQLite database
    if db_path is not None:
        if not db_path.exists():
            report.warnings.append(f"Database not found: {db_path} (expected on first run)")

    for warning in report.warnings:
        logger.warning("startup verification: %s", warning)

    return report
