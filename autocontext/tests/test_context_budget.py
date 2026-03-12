"""Tests for context budget management (MTS-21)."""
from __future__ import annotations

import autocontext.scenarios  # noqa: F401  # pre-import to avoid circular import through prompts.__init__
from autocontext.prompts.context_budget import ContextBudget, estimate_tokens


def test_estimate_tokens_basic() -> None:
    assert estimate_tokens("hello world") == 2  # 11 chars // 4


def test_estimate_tokens_empty() -> None:
    assert estimate_tokens("") == 0


def test_budget_no_trimming_when_under() -> None:
    budget = ContextBudget(max_tokens=1000)
    components = {
        "playbook": "Short playbook.",
        "trajectory": "Gen 1: 0.5",
        "lessons": "- lesson one",
        "tools": "tool_a: does X",
        "analysis": "Analysis text.",
        "hints": "Try X.",
    }
    trimmed = budget.apply(components)
    assert trimmed == components


def test_budget_trims_trajectory_first() -> None:
    budget = ContextBudget(max_tokens=20)
    components = {
        "playbook": "Short.",
        "trajectory": "A" * 200,
        "lessons": "B" * 40,
        "tools": "C" * 40,
        "analysis": "D" * 40,
        "hints": "Hint.",
    }
    trimmed = budget.apply(components)
    assert len(trimmed["trajectory"]) < len(components["trajectory"])


def test_budget_cascade_order() -> None:
    """Cascade trims in order: trajectory, analysis, tools, lessons, playbook."""
    budget = ContextBudget(max_tokens=5)
    components = {
        "playbook": "P" * 100,
        "trajectory": "T" * 100,
        "lessons": "L" * 100,
        "tools": "O" * 100,
        "analysis": "A" * 100,
        "hints": "H" * 20,
    }
    trimmed = budget.apply(components)
    assert len(trimmed["trajectory"]) <= len(trimmed["playbook"])


def test_budget_preserves_hints() -> None:
    """Hints are never trimmed."""
    budget = ContextBudget(max_tokens=5)
    components = {
        "playbook": "P" * 100,
        "trajectory": "T" * 100,
        "lessons": "L" * 100,
        "tools": "O" * 100,
        "analysis": "A" * 100,
        "hints": "Keep this hint.",
    }
    trimmed = budget.apply(components)
    assert trimmed["hints"] == "Keep this hint."
