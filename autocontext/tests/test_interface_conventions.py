"""Tests for interface conventions (AC-492).

Enforces that ABC and Protocol are used consistently per CONTRIBUTING.md.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autocontext"


def _find_classes(base_name: str) -> list[tuple[str, str]]:
    """Find all classes inheriting from base_name across the codebase."""
    results = []
    for root, dirs, files in os.walk(SRC_ROOT):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__")]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = Path(root) / f
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        name = ""
                        if isinstance(base, ast.Name):
                            name = base.id
                        elif isinstance(base, ast.Attribute):
                            name = base.attr
                        if name == base_name:
                            rel = str(path.relative_to(SRC_ROOT))
                            results.append((rel, node.name))
    return results


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
                            (isinstance(b, ast.Name) and b.id == "Protocol")
                            for b in node.bases
                        )
                        if is_protocol:
                            for item in ast.walk(node):
                                if isinstance(item, ast.FunctionDef):
                                    for dec in item.decorator_list:
                                        dec_name = ""
                                        if isinstance(dec, ast.Name):
                                            dec_name = dec.id
                                        if dec_name == "abstractmethod":
                                            rel = str(path.relative_to(SRC_ROOT))
                                            violations.append(
                                                f"{rel}:{node.name}.{item.name} — Protocol should not use @abstractmethod"
                                            )

        assert violations == [], "\n".join(violations)

    def test_abc_classes_have_abstractmethod(self) -> None:
        """ABC subclasses should have at least one @abstractmethod."""
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
                        is_abc = any(
                            (isinstance(b, ast.Name) and b.id == "ABC")
                            for b in node.bases
                        )
                        if is_abc:
                            has_abstract = False
                            for item in node.body:
                                if isinstance(item, ast.FunctionDef):
                                    for dec in item.decorator_list:
                                        if isinstance(dec, ast.Name) and dec.id == "abstractmethod":
                                            has_abstract = True
                            if not has_abstract:
                                rel = str(path.relative_to(SRC_ROOT))
                                violations.append(f"{rel}:{node.name} — ABC without @abstractmethod")

        assert violations == [], (
            "ABC classes without @abstractmethod (use Protocol if no abstract methods needed):\n"
            + "\n".join(f"  {v}" for v in violations)
        )
