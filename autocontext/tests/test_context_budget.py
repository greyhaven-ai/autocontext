"""Tests for context budget management (AC-21)."""
from __future__ import annotations

import autocontext.scenarios  # noqa: F401  # pre-import to avoid circular import through prompts.__init__
from autocontext.prompts.context_budget import ContextBudget, ContextBudgetPolicy, estimate_tokens


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


def test_budget_deduplicates_equivalent_components_by_policy() -> None:
    """Duplicate context is selected once, keeping the highest-priority source."""
    duplicate = "Use the stable rollback guard."
    budget = ContextBudget(max_tokens=1000)
    components = {
        "playbook": duplicate,
        "analysis": duplicate,
        "trajectory": "Gen 1: 0.5",
        "hints": duplicate,
    }

    trimmed = budget.apply(components)

    assert trimmed["playbook"] == duplicate
    assert trimmed["analysis"] == ""
    assert trimmed["hints"] == duplicate


def test_budget_does_not_deduplicate_role_scoped_components() -> None:
    """Role-scoped alternatives are used by separate final prompts, not together."""
    duplicate = "Role-scoped evidence that multiple roles should receive."
    budget = ContextBudget(max_tokens=1000)
    components = {
        "evidence_manifest_analyst": duplicate,
        "evidence_manifest_architect": duplicate,
        "notebook_analyst": duplicate,
        "notebook_architect": duplicate,
    }

    trimmed = budget.apply(components)

    assert trimmed == components


def test_budget_applies_component_caps_before_global_trim() -> None:
    """Bulky low-priority components are capped even when the global budget fits."""
    budget = ContextBudget(
        max_tokens=1000,
        policy=ContextBudgetPolicy(component_token_caps={"analysis": 5}),
    )
    components = {
        "playbook": "small playbook",
        "analysis": "A" * 200,
    }

    trimmed = budget.apply(components)

    assert trimmed["playbook"] == "small playbook"
    assert len(trimmed["analysis"]) < len(components["analysis"])
    assert estimate_tokens(trimmed["analysis"]) <= 5


def test_budget_policy_overrides_trim_order_and_protected_components() -> None:
    """The budget policy owns domain-specific trim order and protection."""
    budget = ContextBudget(
        max_tokens=10,
        policy=ContextBudgetPolicy(
            trim_order=("playbook", "analysis"),
            protected_components=frozenset({"analysis"}),
            component_token_caps={},
        ),
    )
    components = {
        "playbook": "P" * 200,
        "analysis": "A" * 200,
    }

    trimmed = budget.apply(components)

    assert len(trimmed["playbook"]) < len(components["playbook"])
    assert trimmed["analysis"] == components["analysis"]
