"""Tests for module size limits (AC-482).

Enforces that no single source module exceeds the LOC threshold.
"""

from __future__ import annotations

import os
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autocontext"

# Maximum lines per source file. 800 is the target for actively-maintained modules.
# Files in GRANDFATHERED are allowed higher limits until they're refactored.
MAX_LINES = 800

GRANDFATHERED: dict[str, int] = {
    # These are large but not yet split — tracked for future refactoring
    "storage/sqlite_store.py": 1600,
    "storage/artifacts.py": 1300,
    "cli.py": 1600,
    "mcp/tools.py": 1500,
    "loop/generation_runner.py": 1400,
    "loop/stages.py": 1400,  # 8 cohesive stage functions; helpers extracted to stage_helpers/
    "agents/orchestrator.py": 1000,
    "execution/task_runner.py": 1000,
    "scenarios/custom/family_pipeline.py": 1000,
    "knowledge/research_hub.py": 1000,
}


class TestModuleSizeLimits:
    """No source file should exceed the LOC limit."""

    def test_no_oversized_modules(self) -> None:
        violations: list[str] = []
        for root, dirs, files in os.walk(SRC_ROOT):
            dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = Path(root) / f
                rel = str(path.relative_to(SRC_ROOT))
                lines = sum(1 for _ in path.open())
                limit = GRANDFATHERED.get(rel, MAX_LINES)
                if lines > limit:
                    violations.append(f"{rel}: {lines} lines (limit {limit})")

        assert violations == [], (
            "Modules exceeding size limits:\n" + "\n".join(f"  {v}" for v in violations)
        )

    def test_stages_helpers_exist(self) -> None:
        """loop/stage_helpers/ should exist with extracted helper modules."""
        helpers_dir = SRC_ROOT / "loop" / "stage_helpers"
        assert helpers_dir.is_dir(), "loop/stage_helpers/ package missing"
        helper_files = list(helpers_dir.glob("*.py"))
        # Expect __init__.py + 6 helper modules
        assert len(helper_files) >= 7, (
            f"Expected 7+ files in stage_helpers/, found {len(helper_files)}: "
            + ", ".join(f.name for f in helper_files)
        )

    def test_stages_under_grandfathered_limit(self) -> None:
        """loop/stages.py should be under its grandfathered limit."""
        stages_path = SRC_ROOT / "loop" / "stages.py"
        lines = sum(1 for _ in stages_path.open())
        limit = GRANDFATHERED["loop/stages.py"]
        assert lines <= limit, (
            f"loop/stages.py is {lines} lines (limit {limit}). "
            f"Extract more helpers into loop/stage_helpers/."
        )
