"""Tests for SampleStateGenerator — diverse game state generation for harness testing."""
from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

import pytest

from autocontext.execution.sample_states import SampleState, SampleStateGenerator
from autocontext.scenarios.base import Observation, Result, ScenarioInterface

# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeScenario(ScenarioInterface):
    """Minimal scenario that runs for a configurable number of turns."""

    name = "fake"

    def __init__(self, max_turns: int = 10) -> None:
        self._max_turns = max_turns

    def describe_rules(self) -> str:
        return "Fake scenario for testing."

    def describe_strategy_interface(self) -> str:
        return "JSON with 'value' float in [0,1]."

    def describe_evaluation_criteria(self) -> str:
        return "Maximize value."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "turn": 0, "terminal": False, "max_turns": self._max_turns}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(narrative=f"Turn {state['turn']}", state=dict(state))

    def validate_actions(
        self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return True, "ok"

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        turn = int(state["turn"]) + 1
        max_turns = int(state["max_turns"])
        return {**dict(state), "turn": turn, "terminal": turn >= max_turns}

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        return Result(score=0.5, summary="done")

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "replay"

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {"turn": state.get("turn", 0)}


class FakeScenarioWithLegalActions(FakeScenario):
    """Fake scenario that also supports enumerate_legal_actions."""

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        if self.is_terminal(state):
            return []
        return [
            {"action": "up", "description": "Move up"},
            {"action": "down", "description": "Move down"},
        ]


# ── SampleState dataclass ────────────────────────────────────────────────────


class TestSampleState:
    def test_fields(self) -> None:
        s = SampleState(
            state={"turn": 3},
            description="Mid-game state",
            expected_legal_actions=[{"action": "up"}],
            difficulty="mid",
        )
        assert s.state == {"turn": 3}
        assert s.description == "Mid-game state"
        assert s.expected_legal_actions == [{"action": "up"}]
        assert s.difficulty == "mid"

    def test_frozen(self) -> None:
        s = SampleState(state={}, description="x", expected_legal_actions=None, difficulty="early")
        with pytest.raises(AttributeError):
            s.difficulty = "late"  # type: ignore[misc]

    def test_none_legal_actions(self) -> None:
        s = SampleState(state={}, description="x", expected_legal_actions=None, difficulty="early")
        assert s.expected_legal_actions is None


# ── SampleStateGenerator.generate ─────────────────────────────────────────────


class TestSampleStateGeneratorGenerate:
    def test_generates_at_least_n_states(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=20), n_states=50)
        states = gen.generate()
        assert len(states) >= 50

    def test_default_50_states(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=20))
        states = gen.generate()
        assert len(states) >= 50

    def test_all_states_have_description(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=20), n_states=10)
        states = gen.generate()
        for s in states:
            assert isinstance(s.description, str)
            assert len(s.description) > 0

    def test_all_states_have_valid_difficulty(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=20), n_states=10)
        states = gen.generate()
        valid = {"early", "mid", "late"}
        for s in states:
            assert s.difficulty in valid

    def test_covers_all_phases(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=30), n_states=30)
        states = gen.generate()
        phases = {s.difficulty for s in states}
        assert "early" in phases
        assert "mid" in phases
        assert "late" in phases

    def test_states_are_dicts(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=10), n_states=5)
        states = gen.generate()
        for s in states:
            assert isinstance(s.state, dict)

    def test_no_legal_actions_without_enumeration(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=10), n_states=5)
        states = gen.generate()
        for s in states:
            assert s.expected_legal_actions is None

    def test_small_n_states(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=5), n_states=3)
        states = gen.generate()
        assert len(states) >= 3

    def test_caching(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=10), n_states=10)
        first = gen.generate()
        second = gen.generate()
        # Cached: same object list returned
        assert first is second


# ── SampleStateGenerator.generate_with_ground_truth ───────────────────────────


class TestSampleStateGeneratorGroundTruth:
    def test_includes_legal_actions(self) -> None:
        gen = SampleStateGenerator(FakeScenarioWithLegalActions(max_turns=20), n_states=10)
        states = gen.generate_with_ground_truth()
        non_terminal = [s for s in states if s.difficulty != "late" or s.expected_legal_actions]
        assert len(non_terminal) > 0
        for s in non_terminal:
            if s.expected_legal_actions is not None:
                assert isinstance(s.expected_legal_actions, list)

    def test_ground_truth_has_actions(self) -> None:
        gen = SampleStateGenerator(FakeScenarioWithLegalActions(max_turns=20), n_states=10)
        states = gen.generate_with_ground_truth()
        has_actions = [s for s in states if s.expected_legal_actions is not None and len(s.expected_legal_actions) > 0]
        assert len(has_actions) > 0

    def test_scenario_without_enumeration_returns_none(self) -> None:
        gen = SampleStateGenerator(FakeScenario(max_turns=10), n_states=5)
        states = gen.generate_with_ground_truth()
        for s in states:
            assert s.expected_legal_actions is None


# ── Works with real scenarios ─────────────────────────────────────────────────


class TestSampleStateGeneratorRealScenarios:
    def test_grid_ctf(self) -> None:
        from autocontext.scenarios.grid_ctf import GridCtfScenario

        scenario = GridCtfScenario()
        gen = SampleStateGenerator(scenario, n_states=10)
        states = gen.generate()
        assert len(states) >= 10

    def test_othello(self) -> None:
        from autocontext.scenarios.othello import OthelloScenario

        scenario = OthelloScenario()
        gen = SampleStateGenerator(scenario, n_states=10)
        states = gen.generate()
        assert len(states) >= 10

    def test_grid_ctf_with_ground_truth(self) -> None:
        from autocontext.scenarios.grid_ctf import GridCtfScenario

        scenario = GridCtfScenario()
        gen = SampleStateGenerator(scenario, n_states=10)
        states = gen.generate_with_ground_truth()
        has_actions = [s for s in states if s.expected_legal_actions is not None]
        assert len(has_actions) > 0

    def test_grid_ctf_random_actions_respect_constraints(self) -> None:
        from autocontext.scenarios.grid_ctf import GridCtfScenario

        scenario = GridCtfScenario()
        gen = SampleStateGenerator(scenario, n_states=1)
        rng = random.Random(7)
        state = scenario.initial_state(seed=7)

        for _ in range(25):
            actions = gen._random_actions(state, rng)
            assert actions is not None
            valid, _ = scenario.validate_actions(state, "generator", actions)
            assert valid
