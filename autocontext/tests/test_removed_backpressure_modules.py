"""Guards intentional removal of the legacy backpressure shim."""

from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "autocontext"
TEST_ROOT = PROJECT_ROOT / "tests"

REMOVED_MODULES = (
    "autocontext.backpressure",
    "autocontext.backpressure.gate",
    "autocontext.backpressure.retry_context",
    "autocontext.backpressure.trend_gate",
)


def _iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in {".venv", "__pycache__"}]
        for name in names:
            if name.endswith(".py"):
                files.append(Path(current_root) / name)
    return files


def _removed_import_lines(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in REMOVED_MODULES:
                    hits.append(f"{alias.name}:{node.lineno}")
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module in REMOVED_MODULES:
                hits.append(f"{node.module}:{node.lineno}")
    return hits


@pytest.mark.parametrize("module_name", REMOVED_MODULES)
def test_removed_backpressure_modules_are_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_no_internal_imports_of_removed_backpressure_modules() -> None:
    violations: list[str] = []
    for path in _iter_python_files(SRC_ROOT) + _iter_python_files(TEST_ROOT):
        lines = _removed_import_lines(path)
        if lines:
            rel = path.relative_to(PROJECT_ROOT)
            violations.append(f"{rel}: {', '.join(lines)}")

    assert violations == [], (
        "Removed backpressure shim modules should not be imported anywhere in source or tests:\n"
        + "\n".join(f"  {entry}" for entry in violations)
    )
