"""Tests for interface conventions (AC-492).

Enforces that ABC and Protocol are used consistently per CONTRIBUTING.md.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autocontext"

def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _decorator_name(decorator: ast.expr) -> str:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    return ""


class TestABCProtocolConventions:
    """ABC for internal hierarchies, Protocol for duck-typed integration."""

    def test_protocols_do_not_use_abstractmethod(self) -> None:
        """Protocol classes should not use @abstractmethod (it's redundant)."""
        violations: list[str] = []
        for root, dirs, files in os.walk(SRC_ROOT):
            dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = Path(root) / f
                source = path.read_text(encoding="utf-8")
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        is_protocol = any(
                            _base_name(base) == "Protocol"
                            for base in node.bases
                        )
                        if is_protocol:
                            for item in ast.walk(node):
                                if isinstance(item, ast.FunctionDef):
                                    for dec in item.decorator_list:
                                        if _decorator_name(dec) == "abstractmethod":
                                            rel = str(path.relative_to(SRC_ROOT))
                                            violations.append(
                                                f"{rel}:{node.name}.{item.name} — Protocol should not use @abstractmethod"
                                            )

        assert violations == [], "\n".join(violations)

    def test_root_abc_classes_have_abstractmethod(self) -> None:
        """Root ABCs should define an abstract contract directly."""
        violations: list[str] = []
        for root, dirs, files in os.walk(SRC_ROOT):
            dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = Path(root) / f
                source = path.read_text(encoding="utf-8")
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        base_names = [_base_name(base) for base in node.bases]
                        if "ABC" in base_names and all(base == "ABC" for base in base_names):
                            has_abstract = False
                            for item in node.body:
                                if isinstance(item, ast.FunctionDef):
                                    for dec in item.decorator_list:
                                        if _decorator_name(dec) == "abstractmethod":
                                            has_abstract = True
                            if not has_abstract:
                                rel = str(path.relative_to(SRC_ROOT))
                                violations.append(f"{rel}:{node.name} — root ABC without @abstractmethod")

        assert violations == [], (
            "Root ABC classes without @abstractmethod (use Protocol if no abstract contract is needed):\n"
            + "\n".join(f"  {v}" for v in violations)
        )
