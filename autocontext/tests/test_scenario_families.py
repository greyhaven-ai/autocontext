"""Tests for AC-245: Scenario-family registry and typed scenario creation contracts.

Validates the ScenarioFamily abstraction, FAMILY_REGISTRY, registration,
lookup, introspection, and detection helpers so that creation pipelines
target explicit families instead of ad-hoc heuristics.
"""

from __future__ import annotations

from typing import Any

import pytest

from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.families import (
    FAMILY_REGISTRY,
    ScenarioFamily,
    detect_family,
    get_family,
    list_families,
    register_family,
)
from autocontext.scenarios.simulation import SimulationInterface

# ---------------------------------------------------------------------------
# Helpers — minimal concrete subclasses for detection tests
# ---------------------------------------------------------------------------


class _StubGameScenario(ScenarioInterface):
    name = "stub_game"

    def describe_rules(self) -> str:
        return ""

    def describe_strategy_interface(self) -> str:
        return ""

    def describe_evaluation_criteria(self) -> str:
        return ""

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {}

    def get_observation(self, state: Any, player_id: str) -> Any:
        return None  # type: ignore[return-value]

    def validate_actions(self, state: Any, player_id: str, actions: Any) -> tuple[bool, str]:
        return True, ""

    def step(self, state: Any, actions: Any) -> dict[str, Any]:
        return {}

    def is_terminal(self, state: Any) -> bool:
        return True

    def get_result(self, state: Any) -> Any:
        return None  # type: ignore[return-value]

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return ""

    def render_frame(self, state: Any) -> dict[str, Any]:
        return {}


class _StubAgentTask(AgentTaskInterface):
    def get_task_prompt(self, state: dict) -> str:
        return "prompt"

    def evaluate_output(self, output: str, state: dict, **kwargs: Any) -> AgentTaskResult:
        return AgentTaskResult(score=0.5, reasoning="ok")

    def get_rubric(self) -> str:
        return "rubric"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "stub task"


class _StubSimulation(SimulationInterface):
    name = "stub_sim"

    def describe_scenario(self) -> str:
        return ""

    def describe_environment(self) -> Any:
        from autocontext.scenarios.simulation import ActionSpec, EnvironmentSpec

        return EnvironmentSpec(
            name="stub",
            description="stub",
            available_actions=[ActionSpec(name="noop", description="noop", parameters={})],
            initial_state_description="empty",
            success_criteria=["done"],
        )

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "step": 0}

    def get_available_actions(self, state: dict[str, Any]) -> list:
        from autocontext.scenarios.simulation import ActionSpec

        return [ActionSpec(name="noop", description="noop", parameters={})]

    def execute_action(self, state: dict[str, Any], action: Any) -> tuple:
        from autocontext.scenarios.simulation import ActionResult

        return ActionResult(success=True, output="ok", state_changes={}), {**state, "step": state.get("step", 0) + 1}

    def is_terminal(self, state: Any) -> bool:
        return dict(state).get("step", 0) >= 1

    def evaluate_trace(self, trace: Any, final_state: dict[str, Any]) -> Any:
        from autocontext.scenarios.simulation import SimulationResult

        return SimulationResult(
            score=1.0, reasoning="ok", dimension_scores={},
            workflow_complete=True, actions_taken=1, actions_successful=1,
        )

    def get_rubric(self) -> str:
        return "rubric"


# ---------------------------------------------------------------------------
# ScenarioFamily dataclass
# ---------------------------------------------------------------------------


class TestScenarioFamily:
    def test_construction(self) -> None:
        family = ScenarioFamily(
            name="game",
            description="Tournament-evaluated game scenarios",
            interface_class=ScenarioInterface,
            evaluation_mode="tournament",
            output_modes=["json_strategy"],
            scenario_type_marker="parametric",
        )
        assert family.name == "game"
        assert family.interface_class is ScenarioInterface
        assert family.evaluation_mode == "tournament"
        assert family.output_modes == ["json_strategy"]
        assert family.scenario_type_marker == "parametric"

    def test_defaults(self) -> None:
        family = ScenarioFamily(
            name="test",
            description="test family",
            interface_class=ScenarioInterface,
            evaluation_mode="tournament",
            output_modes=["json_strategy"],
            scenario_type_marker="test",
        )
        assert family.capabilities == []
        assert family.supports_knowledge_accumulation is True
        assert family.supports_playbook is False

    def test_with_capabilities(self) -> None:
        family = ScenarioFamily(
            name="game",
            description="Game scenarios",
            interface_class=ScenarioInterface,
            evaluation_mode="tournament",
            output_modes=["json_strategy"],
            scenario_type_marker="parametric",
            capabilities=["elo_ranking", "playbook"],
            supports_playbook=True,
        )
        assert "elo_ranking" in family.capabilities
        assert family.supports_playbook is True


# ---------------------------------------------------------------------------
# Registry: register, get, list
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_family(self) -> None:
        family = ScenarioFamily(
            name="_test_custom",
            description="Custom test family",
            interface_class=ScenarioInterface,
            evaluation_mode="custom",
            output_modes=["custom"],
            scenario_type_marker="_test_custom",
        )
        register_family(family)
        try:
            assert "_test_custom" in FAMILY_REGISTRY
            assert FAMILY_REGISTRY["_test_custom"] is family
        finally:
            FAMILY_REGISTRY.pop("_test_custom", None)

    def test_register_duplicate_raises(self) -> None:
        family = ScenarioFamily(
            name="_test_dup",
            description="dup",
            interface_class=ScenarioInterface,
            evaluation_mode="tournament",
            output_modes=[],
            scenario_type_marker="_test_dup",
        )
        register_family(family)
        try:
            with pytest.raises(ValueError, match="already registered"):
                register_family(family)
        finally:
            FAMILY_REGISTRY.pop("_test_dup", None)

    def test_get_family_exists(self) -> None:
        result = get_family("game")
        assert result.name == "game"
        assert result.interface_class is ScenarioInterface

    def test_get_family_not_found(self) -> None:
        with pytest.raises(KeyError, match="Unknown scenario family"):
            get_family("nonexistent_family")

    def test_list_families(self) -> None:
        families = list_families()
        assert len(families) >= 3
        names = {f.name for f in families}
        assert "game" in names
        assert "agent_task" in names
        assert "simulation" in names


# ---------------------------------------------------------------------------
# Built-in families: game, agent_task, simulation
# ---------------------------------------------------------------------------


class TestBuiltinFamilies:
    def test_game_family(self) -> None:
        game = get_family("game")
        assert game.interface_class is ScenarioInterface
        assert game.evaluation_mode == "tournament"
        assert game.supports_playbook is True
        assert "json_strategy" in game.output_modes

    def test_agent_task_family(self) -> None:
        task = get_family("agent_task")
        assert task.interface_class is AgentTaskInterface
        assert task.evaluation_mode == "llm_judge"
        assert task.supports_playbook is False
        assert "free_text" in task.output_modes

    def test_simulation_family(self) -> None:
        sim = get_family("simulation")
        assert sim.interface_class is SimulationInterface
        assert sim.evaluation_mode == "trace_evaluation"
        assert sim.supports_playbook is True
        assert "action_trace" in sim.output_modes

    def test_game_scenario_type_marker(self) -> None:
        game = get_family("game")
        assert game.scenario_type_marker == "parametric"

    def test_agent_task_scenario_type_marker(self) -> None:
        task = get_family("agent_task")
        assert task.scenario_type_marker == "agent_task"

    def test_simulation_scenario_type_marker(self) -> None:
        sim = get_family("simulation")
        assert sim.scenario_type_marker == "simulation"


# ---------------------------------------------------------------------------
# detect_family() — detect family from instance
# ---------------------------------------------------------------------------


class TestDetectFamily:
    def test_detect_game_scenario(self) -> None:
        scenario = _StubGameScenario()
        family = detect_family(scenario)
        assert family is not None
        assert family.name == "game"

    def test_detect_agent_task(self) -> None:
        task = _StubAgentTask()
        family = detect_family(task)
        assert family is not None
        assert family.name == "agent_task"

    def test_detect_simulation(self) -> None:
        sim = _StubSimulation()
        family = detect_family(sim)
        assert family is not None
        assert family.name == "simulation"

    def test_detect_unknown_returns_none(self) -> None:
        family = detect_family("not a scenario")  # type: ignore[arg-type]
        assert family is None

    def test_simulation_detected_before_game(self) -> None:
        """SimulationInterface extends ScenarioInterface — simulation must match first."""
        sim = _StubSimulation()
        family = detect_family(sim)
        assert family is not None
        assert family.name == "simulation"  # NOT "game"


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


class TestIntrospection:
    def test_family_has_description(self) -> None:
        for family in list_families():
            assert family.description, f"Family '{family.name}' has no description"

    def test_family_has_evaluation_mode(self) -> None:
        for family in list_families():
            assert family.evaluation_mode, f"Family '{family.name}' has no evaluation_mode"

    def test_family_has_output_modes(self) -> None:
        for family in list_families():
            assert len(family.output_modes) >= 1, f"Family '{family.name}' has no output_modes"

    def test_all_families_have_distinct_markers(self) -> None:
        families = list_families()
        markers = [f.scenario_type_marker for f in families]
        assert len(markers) == len(set(markers)), "Duplicate scenario_type_markers found"


# ---------------------------------------------------------------------------
# Registry integration with SCENARIO_REGISTRY
# ---------------------------------------------------------------------------


class TestScenarioRegistryIntegration:
    def test_detect_family_for_builtin_grid_ctf(self) -> None:
        from autocontext.scenarios import SCENARIO_REGISTRY

        cls = SCENARIO_REGISTRY.get("grid_ctf")
        assert cls is not None
        instance = cls()
        family = detect_family(instance)
        assert family is not None
        assert family.name == "game"

    def test_detect_family_for_builtin_othello(self) -> None:
        from autocontext.scenarios import SCENARIO_REGISTRY

        cls = SCENARIO_REGISTRY.get("othello")
        assert cls is not None
        instance = cls()
        family = detect_family(instance)
        assert family is not None
        assert family.name == "game"
