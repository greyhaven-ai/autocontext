"""Tests for serialization conventions (AC-489).

Enforces that to_dict/from_dict methods delegate to Pydantic model_dump/model_validate
rather than reimplementing serialization by hand.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autocontext"

# Modules already migrated to Pydantic — their to_dict should be 1-line delegations
MIGRATED_DIRS = {"analytics", "knowledge", "harness"}


def _count_manual_serde(directory: Path) -> list[tuple[str, str, int]]:
    """Find to_dict/from_dict methods that are NOT 1-line Pydantic delegations.

    Returns list of (file, class.method, body_lines).
    """
    violations = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__")]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = Path(root) / f
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError):
                continue

            for cls_node in ast.walk(tree):
                if not isinstance(cls_node, ast.ClassDef):
                    continue
                for item in cls_node.body:
                    if not isinstance(item, ast.FunctionDef):
                        continue
                    if item.name not in ("to_dict", "from_dict"):
                        continue
                    # Count non-empty, non-docstring body lines
                    body = item.body
                    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                        body = body[1:]  # skip docstring
                    if len(body) == 1:
                        # Check if it's a model_dump/model_validate delegation
                        stmt = body[0]
                        src_segment = ast.get_source_segment(source, stmt) or ""
                        if "model_dump" in src_segment or "model_validate" in src_segment:
                            continue  # This is a proper Pydantic delegation
                    rel = str(path.relative_to(SRC_ROOT))
                    violations.append((rel, f"{cls_node.name}.{item.name}", len(body)))
    return violations


class TestOverallSerdeBudget:
    """Track total manual to_dict/from_dict across the codebase."""

    def test_total_manual_serde_under_budget(self) -> None:
        all_violations = _count_manual_serde(SRC_ROOT)
        total = len(all_violations)
        # Budget: enforce continued reduction. Started at 295 manual methods,
        # reduced to 170 via analytics/knowledge/harness migration.
        # Target after execution migration: ~135
        assert total <= 135, (
            f"Total manual to_dict/from_dict: {total} (budget: 100)\n"
            + "\n".join(f"  {f}:{m}" for f, m, _ in all_violations[:20])
        )


class TestExecutionModuleSerde:
    """execution/ should use Pydantic serde after migration."""

    def test_execution_uses_pydantic_serde(self) -> None:
        violations = _count_manual_serde(SRC_ROOT / "execution")
        # PhasedExecutionResult.to_dict wraps model_dump() + computed properties
        violations = [(f, m, n) for f, m, n in violations if "PhasedExecutionResult" not in m]
        assert violations == [], (
            "execution/ has manual to_dict/from_dict:\n"
            + "\n".join(f"  {f}:{m} ({n} lines)" for f, m, n in violations)
        )
