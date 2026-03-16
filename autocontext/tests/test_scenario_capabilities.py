"""Tests for AC-144: typed scenario capability adapters.

Covers: ScenarioCapabilities, resolve_capabilities, get_description,
get_evaluation_criteria, can_validate_actions, can_run_match,
get_task_prompt_safe, get_rubric_safe.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Lightweight test doubles
# ---------------------------------------------------------------------------


class _MockGameScenario:
    """Mimics ScenarioInterface with duck-typed methods."""

    name = "mock_game"

    def describe_rules(self) -> str:
        return "Game rules: capture the flag."

    def describe_strategy_interface(self) -> str:
        return '{"aggression": float, "defense": float}'

    def describe_evaluation_criteria(self) -> str:
        return "Maximize flag captures while defending."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"grid": [], "turn": 0}

    def validate_actions(self, state: Any, player_id: str, actions: Any) -> tuple[bool, str]:
        return True, "ok"

    def execute_match(self, strategy: Any, seed: int) -> Any:
        return {"score": 0.7, "winner": "challenger"}


class _MockAgentTask:
    """Mimics AgentTaskInterface with duck-typed methods."""

    name = "mock_task"

    def get_task_prompt(self, state: dict[str, Any]) -> str:
        return "Write a Python function that sorts a list."

    def evaluate_output(self, output: str, state: dict[str, Any], **kwargs: Any) -> Any:
        return {"score": 0.8}

    def get_rubric(self) -> str:
        return "Correctness, efficiency, readability."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"task_name": "sort_list"}

    def describe_task(self) -> str:
        return "Sort a list of integers."


class _MockBareScenario:
    """Minimal scenario with almost no capabilities."""

    name = "mock_bare"

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {}


# ===========================================================================
# ScenarioCapabilities
# ===========================================================================


class TestScenarioCapabilities:
    def test_construction(self) -> None:
        from autocontext.scenarios.capabilities import ScenarioCapabilities

        caps = ScenarioCapabilities(
            describable=True,
            action_validating=True,
            match_runnable=True,
            task_bearing=False,
            rubric_bearing=False,
            is_game=True,
            is_agent_task=False,
        )
        assert caps.describable is True
        assert caps.is_game is True
        assert caps.is_agent_task is False


# ===========================================================================
# resolve_capabilities
# ===========================================================================


class TestResolveCapabilities:
    def test_game_scenario(self) -> None:
        from autocontext.scenarios.capabilities import resolve_capabilities

        caps = resolve_capabilities(_MockGameScenario())
        assert caps.describable is True
        assert caps.action_validating is True
        assert caps.match_runnable is True
        assert caps.task_bearing is False
        assert caps.rubric_bearing is False
        assert caps.is_game is True
        assert caps.is_agent_task is False

    def test_agent_task(self) -> None:
        from autocontext.scenarios.capabilities import resolve_capabilities

        caps = resolve_capabilities(_MockAgentTask())
        assert caps.describable is True
        assert caps.action_validating is False
        assert caps.match_runnable is False
        assert caps.task_bearing is True
        assert caps.rubric_bearing is True
        assert caps.is_game is False
        assert caps.is_agent_task is True

    def test_bare_scenario(self) -> None:
        from autocontext.scenarios.capabilities import resolve_capabilities

        caps = resolve_capabilities(_MockBareScenario())
        assert caps.describable is False
        assert caps.action_validating is False
        assert caps.match_runnable is False
        assert caps.task_bearing is False


# ===========================================================================
# get_description
# ===========================================================================


class TestGetDescription:
    def test_game_scenario_uses_rules(self) -> None:
        from autocontext.scenarios.capabilities import get_description

        desc = get_description(_MockGameScenario())
        assert "capture the flag" in desc

    def test_agent_task_uses_describe_task(self) -> None:
        from autocontext.scenarios.capabilities import get_description

        desc = get_description(_MockAgentTask())
        assert "Sort a list" in desc

    def test_bare_returns_empty(self) -> None:
        from autocontext.scenarios.capabilities import get_description

        desc = get_description(_MockBareScenario())
        assert desc == ""


# ===========================================================================
# get_evaluation_criteria
# ===========================================================================


class TestGetEvaluationCriteria:
    def test_game_scenario(self) -> None:
        from autocontext.scenarios.capabilities import get_evaluation_criteria

        criteria = get_evaluation_criteria(_MockGameScenario())
        assert "flag captures" in criteria

    def test_agent_task_returns_rubric(self) -> None:
        from autocontext.scenarios.capabilities import get_evaluation_criteria

        criteria = get_evaluation_criteria(_MockAgentTask())
        assert "Correctness" in criteria

    def test_bare_returns_empty(self) -> None:
        from autocontext.scenarios.capabilities import get_evaluation_criteria

        assert get_evaluation_criteria(_MockBareScenario()) == ""


# ===========================================================================
# can_validate_actions / can_run_match
# ===========================================================================


class TestCanValidateActions:
    def test_game_true(self) -> None:
        from autocontext.scenarios.capabilities import can_validate_actions

        assert can_validate_actions(_MockGameScenario()) is True

    def test_agent_task_false(self) -> None:
        from autocontext.scenarios.capabilities import can_validate_actions

        assert can_validate_actions(_MockAgentTask()) is False

    def test_bare_false(self) -> None:
        from autocontext.scenarios.capabilities import can_validate_actions

        assert can_validate_actions(_MockBareScenario()) is False


class TestCanRunMatch:
    def test_game_true(self) -> None:
        from autocontext.scenarios.capabilities import can_run_match

        assert can_run_match(_MockGameScenario()) is True

    def test_agent_task_false(self) -> None:
        from autocontext.scenarios.capabilities import can_run_match

        assert can_run_match(_MockAgentTask()) is False


# ===========================================================================
# get_task_prompt_safe / get_rubric_safe
# ===========================================================================


class TestGetTaskPromptSafe:
    def test_agent_task_returns_prompt(self) -> None:
        from autocontext.scenarios.capabilities import get_task_prompt_safe

        prompt = get_task_prompt_safe(_MockAgentTask())
        assert prompt is not None
        assert "sorts a list" in prompt

    def test_game_returns_none(self) -> None:
        from autocontext.scenarios.capabilities import get_task_prompt_safe

        assert get_task_prompt_safe(_MockGameScenario()) is None


class TestGetRubricSafe:
    def test_agent_task_returns_rubric(self) -> None:
        from autocontext.scenarios.capabilities import get_rubric_safe

        rubric = get_rubric_safe(_MockAgentTask())
        assert rubric is not None
        assert "Correctness" in rubric

    def test_game_returns_none(self) -> None:
        from autocontext.scenarios.capabilities import get_rubric_safe

        assert get_rubric_safe(_MockGameScenario()) is None


# ===========================================================================
# get_strategy_interface_safe
# ===========================================================================


class TestGetStrategyInterfaceSafe:
    def test_game_returns_interface(self) -> None:
        from autocontext.scenarios.capabilities import get_strategy_interface_safe

        iface = get_strategy_interface_safe(_MockGameScenario())
        assert iface is not None
        assert "aggression" in iface

    def test_agent_task_returns_none(self) -> None:
        from autocontext.scenarios.capabilities import get_strategy_interface_safe

        assert get_strategy_interface_safe(_MockAgentTask()) is None
