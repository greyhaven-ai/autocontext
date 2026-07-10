"""ERP-73 — scenario-contract provenance for structural role isolation.

The scenario contract (rules / interface / criteria) is trusted (system turn)
only for built-in, operator-authored scenarios. Scenarios produced by the
`solve` LLM path or template codegen load under
`autocontext.scenarios.custom.generated.*`; their contract is
attacker-influenceable and must stay in the untrusted turn.
"""

from __future__ import annotations

from autocontext.scenarios.custom.loader import is_generated_scenario


class _BuiltInLikeScenario:
    """Class defined in this (non-generated) test module."""


def test_builtin_scenario_is_not_generated() -> None:
    assert is_generated_scenario(_BuiltInLikeScenario()) is False


def test_generated_package_scenario_is_detected() -> None:
    generated_cls = type("GeneratedScenario", (), {})
    generated_cls.__module__ = "autocontext.scenarios.custom.generated.some_solve_task"
    assert is_generated_scenario(generated_cls()) is True


def test_generated_agent_task_scenario_is_detected() -> None:
    generated_cls = type("GeneratedAgentTask", (), {})
    generated_cls.__module__ = "autocontext.scenarios.custom.generated.agent_task_foo"
    assert is_generated_scenario(generated_cls()) is True


def test_nongenerated_custom_module_is_not_generated() -> None:
    # A helper under scenarios.custom (but not .generated) is operator code.
    other_cls = type("Helper", (), {})
    other_cls.__module__ = "autocontext.scenarios.custom.registry"
    assert is_generated_scenario(other_cls()) is False


def test_object_without_module_defaults_to_not_generated() -> None:
    # Defensive: never crash on odd objects.
    assert is_generated_scenario(object()) is False
