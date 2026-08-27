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
    "tb_frame", "tb_next", "f_back", "f_builtins", "f_globals",
    "gi_frame", "cr_frame", "ag_frame",
})

_DENIED_NAMES: frozenset[str] = frozenset({
    "eval", "exec", "compile",
    "getattr", "setattr", "delattr",
    "open", "__import__", "breakpoint",
    "globals", "locals", "vars", "dir",
    "type",
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
        if node.attr in _DENIED_ATTRIBUTES or _is_dunder(node.attr):
            self.violations.append(f"denied attribute access: {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id in _DENIED_NAMES or _is_dunder(node.id):
            self.violations.append(f"denied name: {node.id}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # Catch calls to denied names even if assigned to a variable
        if isinstance(node.func, ast.Name) and node.func.id in _DENIED_NAMES:
            self.violations.append(f"denied call: {node.func.id}()")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if _is_dunder(node.name):
            self.violations.append(f"dunder function definition not allowed: {node.name}")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        if _is_dunder(node.name):
            self.violations.append(f"dunder function definition not allowed: {node.name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        if _is_dunder(node.name):
            self.violations.append(f"dunder class definition not allowed: {node.name}")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:  # noqa: N802
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and _is_dunder(key.value):
                self.violations.append(f"dunder dictionary key not allowed: {key.value}")
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        self.violations.append("context managers are not allowed")
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
        self.violations.append("async context managers are not allowed")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        # SIGALRM-based timeouts raise inside the untrusted frame. Any try
        # statement can catch the timeout or run an unbounded finally block.
        self.violations.append("exception handling is not allowed")
        self.generic_visit(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:  # noqa: N802
        self.violations.append("exception-group handling is not allowed")
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


def _is_dunder(value: str) -> bool:
    return len(value) >= 4 and value.startswith("__") and value.endswith("__")
