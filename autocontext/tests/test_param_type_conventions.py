"""Tests for parameter type conventions (AC-494).

Enforces that public API functions accept the broadest useful input types:
- Read-only list params use Sequence[X] (accepts tuples, generators, etc.)
- Read-only dict params use Mapping[str, X] (accepts frozendict, ChainMap, etc.)

Only covers the core public-facing modules where callers benefit most from
flexible input types. Internal helpers and __init__ methods are excluded.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autocontext"

# Modules that define the public API surface — callers benefit from flexible input types
PUBLIC_API_MODULES = [
    SRC_ROOT / "scenarios" / "base.py",
    SRC_ROOT / "execution" / "judge.py",
    SRC_ROOT / "execution" / "supervisor.py",
    SRC_ROOT / "harness" / "evaluation" / "dimensional.py",
    SRC_ROOT / "harness" / "evaluation" / "self_play.py",
    SRC_ROOT / "harness" / "scoring" / "backends.py",
    SRC_ROOT / "harness" / "validation" / "staged.py",
    SRC_ROOT / "knowledge" / "evidence_freshness.py",
    SRC_ROOT / "knowledge" / "hint_volume.py",
    SRC_ROOT / "knowledge" / "lessons.py",
    SRC_ROOT / "agents" / "orchestrator.py",
    SRC_ROOT / "preflight.py",
    SRC_ROOT / "monitor" / "evaluators.py",
    SRC_ROOT / "consultation" / "triggers.py",
]

MUTATING_LIST_METHODS = {"append", "extend", "insert", "remove", "pop", "sort", "reverse", "clear"}
MUTATING_DICT_METHODS = {"update", "pop", "popitem", "clear", "setdefault"}


def _param_is_mutated(func_node: ast.FunctionDef, param_name: str, mutating_methods: set[str]) -> bool:
    """Check if a parameter is mutated in the function body."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == param_name:
                if node.func.attr in mutating_methods:
                    return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    if target.value.id == param_name:
                        return True
        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == param_name:
                return True
    return False


class TestPublicAPIUsesSequenceForReadOnlyListParams:
    """Public API functions should accept Sequence[X] for read-only list params."""

    @pytest.mark.parametrize("module_path", PUBLIC_API_MODULES, ids=lambda p: p.relative_to(SRC_ROOT).as_posix())
    def test_no_list_params_in_public_functions(self, module_path: Path) -> None:
        if not module_path.exists():
            pytest.skip(f"{module_path} does not exist")

        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        violations: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue

            for arg in node.args.args:
                if arg.annotation and arg.arg != "self":
                    ann_text = ast.get_source_segment(source, arg.annotation)
                    if ann_text and ann_text.startswith("list["):
                        if not _param_is_mutated(node, arg.arg, MUTATING_LIST_METHODS):
                            violations.append(f"{node.name}({arg.arg}: {ann_text}) → use Sequence")

        assert violations == [], (
            "Read-only list params should use Sequence:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


class TestScenarioInterfaceUsesMapping:
    """ScenarioInterface already uses Mapping — verify it stays that way."""

    def test_scenario_interface_uses_mapping_for_state(self) -> None:
        base_path = SRC_ROOT / "scenarios" / "base.py"
        source = base_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "ScenarioInterface":
                for item in ast.walk(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for arg in item.args.args:
                            if arg.annotation and arg.arg in ("state", "actions"):
                                ann_text = ast.get_source_segment(source, arg.annotation)
                                assert ann_text is not None
                                assert "Mapping" in ann_text, (
                                    f"ScenarioInterface.{item.name}({arg.arg}) should use Mapping, "
                                    f"got: {ann_text}"
                                )
