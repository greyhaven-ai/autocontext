"""Tests for gate/guard/validator taxonomy (AC-484).

Enforces that no dead gate/guard/validator implementations exist,
and the taxonomy is clear.
"""

from __future__ import annotations

import os
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autocontext"

# Dead modules that should not exist
DEAD_MODULES = [
    "knowledge/playbook_guard.py",
]


class TestNoDeadGateModules:
    """Dead gate/guard/validator modules should be removed."""

    def test_no_dead_gate_modules(self) -> None:
        remaining = [
            m for m in DEAD_MODULES
            if (SRC_ROOT / m).exists()
        ]
        assert remaining == [], (
            "Dead gate/guard/validator modules still exist:\n"
            + "\n".join(f"  {m}" for m in remaining)
        )


class TestGateTaxonomyIsClean:
    """Each gate/guard/validator should have at least one production import."""

    def test_all_gate_files_are_imported(self) -> None:
        """Every file matching *gate* or *guard* or *valid* should be imported somewhere."""
        gate_files = []
        for root, dirs, files in os.walk(SRC_ROOT):
            dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__")]
            for f in files:
                if f.endswith(".py") and ("gate" in f or "guard" in f or "valid" in f):
                    path = Path(root) / f
                    rel = str(path.relative_to(SRC_ROOT))
                    # Skip stage files (they ARE the consumers, not standalone gates)
                    if rel.startswith("loop/stage_"):
                        continue
                    # Skip __init__.py
                    if f == "__init__.py":
                        continue
                    gate_files.append((rel, path))

        dead: list[str] = []
        for rel, path in gate_files:
            # Check if this module is imported anywhere in src (not just tests)
            module_stem = path.stem
            # Search for imports of this module
            import_found = False
            for root2, dirs2, files2 in os.walk(SRC_ROOT):
                dirs2[:] = [d for d in dirs2 if d not in (".venv", "__pycache__")]
                for f2 in files2:
                    if f2.endswith(".py"):
                        p2 = Path(root2) / f2
                        if p2 == path:
                            continue
                        content = p2.read_text(encoding="utf-8")
                        if module_stem in content:
                            import_found = True
                            break
                if import_found:
                    break

            if not import_found:
                dead.append(rel)

        assert dead == [], (
            "Gate/guard/validator files with no production imports:\n"
            + "\n".join(f"  {d}" for d in dead)
        )
