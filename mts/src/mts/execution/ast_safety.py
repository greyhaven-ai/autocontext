"""AST safety checker — rejects dangerous patterns before code execution.

Walks the AST of architect-generated harness code and flags imports,
dunder attribute access, dangerous builtins, and other escape vectors
that could bypass the restricted-builtins sandbox.
"""
from __future__ import annotations

import ast

_DENIED_ATTRIBUTES: frozenset[str] = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__import__", "__code__",
    "__func__", "__self__", "__dict__",
    "__getattr__", "__setattr__", "__delattr__",
})

_DENIED_NAMES: frozenset[str] = frozenset({
    "eval", "exec", "compile",
    "getattr", "setattr", "delattr",
    "open", "__import__", "breakpoint",
    "globals", "locals", "vars", "dir",
})


class AstSafetyVisitor(ast.NodeVisitor):
    """Collects violations from an AST tree."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        names = ", ".join(alias.name for alias in node.names)
        self.violations.append(f"import statement not allowed: import {names}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        self.violations.append(f"import statement not allowed: from {module} import ...")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr in _DENIED_ATTRIBUTES:
            self.violations.append(f"denied attribute access: {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id in _DENIED_NAMES:
            self.violations.append(f"denied name: {node.id}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # Catch calls to denied names even if assigned to a variable
        if isinstance(node.func, ast.Name) and node.func.id in _DENIED_NAMES:
            self.violations.append(f"denied call: {node.func.id}()")
        self.generic_visit(node)


def check_ast_safety(source: str) -> list[str]:
    """Parse source and return a list of safety violations (empty = safe)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    visitor = AstSafetyVisitor()
    visitor.visit(tree)
    return visitor.violations
