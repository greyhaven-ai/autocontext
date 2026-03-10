"""Tests for grid_ctf enumerate_legal_actions (MTS-85)."""

from __future__ import annotations

from mts.scenarios.grid_ctf.scenario import GridCtfScenario


def _scenario() -> GridCtfScenario:
    return GridCtfScenario()


class TestGridCtfEnumerateLegalActions:
    def test_returns_list_not_none(self) -> None:
        s = _scenario()
        state = s.initial_state(seed=42)
        result = s.enumerate_legal_actions(state)
        assert result is not None

    def test_returns_three_parameters(self) -> None:
        s = _scenario()
        state = s.initial_state(seed=42)
        actions = s.enumerate_legal_actions(state)
        assert actions is not None
        assert len(actions) == 3

    def test_action_names_match_strategy(self) -> None:
        s = _scenario()
        state = s.initial_state(seed=42)
        actions = s.enumerate_legal_actions(state)
        assert actions is not None
        names = [a["action"] for a in actions]
        assert names == ["aggression", "defense", "path_bias"]

    def test_each_action_has_required_fields(self) -> None:
        s = _scenario()
        state = s.initial_state(seed=42)
        actions = s.enumerate_legal_actions(state)
        assert actions is not None
        for action in actions:
            assert "action" in action
            assert "description" in action
            assert isinstance(action["action"], str)
            assert isinstance(action["description"], str)

    def test_continuous_type_and_range(self) -> None:
        s = _scenario()
        state = s.initial_state(seed=42)
        actions = s.enumerate_legal_actions(state)
        assert actions is not None
        for action in actions:
            assert action["type"] == "continuous"
            assert action["range"] == [0.0, 1.0]

    def test_terminal_state_returns_empty(self) -> None:
        s = _scenario()
        state = s.initial_state(seed=42)
        terminal = {**state, "terminal": True}
        actions = s.enumerate_legal_actions(terminal)
        assert actions == []

    def test_deterministic_across_seeds(self) -> None:
        s = _scenario()
        a1 = s.enumerate_legal_actions(s.initial_state(seed=1))
        a2 = s.enumerate_legal_actions(s.initial_state(seed=999))
        assert a1 == a2

    def test_descriptions_are_nonempty(self) -> None:
        s = _scenario()
        actions = s.enumerate_legal_actions(s.initial_state(seed=42))
        assert actions is not None
        for action in actions:
            assert len(action["description"]) > 0
