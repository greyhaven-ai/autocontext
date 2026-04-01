"""Tests for gate/guard/validator taxonomy (AC-484).

Enforces that no dead gate/guard/validator implementations exist,
and the taxonomy is clear.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autocontext"
PACKAGE_NAME = "autocontext"
TAXONOMY_PATTERN = re.compile(r"(^|_)(gate|guard|guardrail|validator)(_|$)")

# Dead modules that should not exist
DEAD_MODULES = [
    "knowledge/playbook_guard.py",
]


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    )


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC_ROOT).with_suffix("")
    return ".".join((PACKAGE_NAME, *rel.parts))


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> str | None:
    current_parts = _module_name(path).split(".")
    package_parts = current_parts[:-1]

    if node.level:
        if node.level - 1 > len(package_parts):
            return None
        base_parts = package_parts[: len(package_parts) - (node.level - 1)]
    else:
        base_parts = []

    module_parts = node.module.split(".") if node.module else []
    resolved_parts = [*base_parts, *module_parts]
    return ".".join(resolved_parts) if resolved_parts else None


def _collect_imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
            continue

        if isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_from(path, node)
            if resolved is None:
                continue
            if node.module:
                imported.add(resolved)
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported.add(f"{resolved}.{alias.name}")

    return imported


class TestNoDeadGateModules:
    """Dead gate/guard/validator modules should be removed."""

    def test_no_dead_gate_modules(self) -> None:
        remaining = [m for m in DEAD_MODULES if (SRC_ROOT / m).exists()]
        assert remaining == [], (
            "Dead gate/guard/validator modules still exist:\n"
            + "\n".join(f"  {m}" for m in remaining)
        )


class TestGateTaxonomyIsClean:
    """Each gate/guard/validator should have at least one production import."""

    def test_all_gate_files_are_imported(self) -> None:
        """Every taxonomy module should be imported somewhere in production code."""
        gate_files: list[tuple[str, Path, str]] = []
        for path in _iter_python_files(SRC_ROOT):
            rel = path.relative_to(SRC_ROOT)
            if path.name == "__init__.py":
                continue
            if rel.as_posix().startswith("loop/stage_"):
                continue
            if not TAXONOMY_PATTERN.search(path.stem):
                continue
            gate_files.append((rel.as_posix(), path, _module_name(path)))

        imported_modules: set[str] = set()
        for path in _iter_python_files(SRC_ROOT):
            imported_modules.update(_collect_imported_modules(path))

        dead: list[str] = []
        for rel, _path, module_name in gate_files:
            if module_name not in imported_modules:
                dead.append(rel)

        assert dead == [], (
            "Gate/guard/validator files with no production imports:\n"
            + "\n".join(f"  {d}" for d in dead)
        )
