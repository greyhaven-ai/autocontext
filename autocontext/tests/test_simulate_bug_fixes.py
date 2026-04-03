"""Tests for AC-520 bug fixes.

1. Abstract class filtering in simulation engine class selection
2. CLI exits non-zero when JSON output has status=failed
"""

from __future__ import annotations

import inspect
import types
from abc import abstractmethod

import pytest


class TestAbstractClassFiltering:
    """Engine should skip abstract classes when selecting scenario class."""

    def test_inspect_isabstract_detects_abstract_class(self) -> None:
        """Verify inspect.isabstract works on our scenario ABCs."""
        from autocontext.scenarios.simulation import SimulationInterface

        assert inspect.isabstract(SimulationInterface)

    def test_inspect_isabstract_detects_operator_loop(self) -> None:
        from autocontext.scenarios.operator_loop import OperatorLoopInterface

        assert inspect.isabstract(OperatorLoopInterface)

    def test_concrete_subclass_is_not_abstract(self) -> None:
        """A concrete subclass should pass the filter."""
        from autocontext.scenarios.simulation import SimulationInterface

        class ConcreteScenario(SimulationInterface):
            def describe_scenario(self): return "test"
            def describe_environment(self): return None  # type: ignore[return-value]
            def initial_state(self, seed=None): return {}
            def get_available_actions(self, state): return []
            def execute_action(self, state, action): return None, state  # type: ignore[return-value]
            def is_terminal(self, state): return True
            def evaluate_trace(self, trace, final_state): return None  # type: ignore[return-value]
            def get_rubric(self): return "rubric"

        assert not inspect.isabstract(ConcreteScenario)

    def test_find_scenario_class_skips_abstract(self) -> None:
        """The helper should skip abstract classes and find concrete ones."""
        from autocontext.scenarios.simulation import SimulationInterface
        from autocontext.simulation.engine import _find_scenario_class

        class AbstractMiddle(SimulationInterface):
            @abstractmethod
            def custom_method(self): ...

        class ConcreteEnd(AbstractMiddle):
            def describe_scenario(self): return "test"
            def describe_environment(self): return None  # type: ignore[return-value]
            def initial_state(self, seed=None): return {}
            def get_available_actions(self, state): return []
            def execute_action(self, state, action): return None, state  # type: ignore[return-value]
            def is_terminal(self, state): return True
            def evaluate_trace(self, trace, final_state): return None  # type: ignore[return-value]
            def get_rubric(self): return "rubric"
            def custom_method(self): return "done"

        # Build a fake module namespace
        mod = types.ModuleType("fake_mod")
        mod.AbstractMiddle = AbstractMiddle  # type: ignore[attr-defined]
        mod.ConcreteEnd = ConcreteEnd  # type: ignore[attr-defined]
        mod.SimulationInterface = SimulationInterface  # type: ignore[attr-defined]

        found = _find_scenario_class(mod)
        assert found is ConcreteEnd

    def test_find_scenario_class_returns_none_when_all_abstract(self) -> None:
        from autocontext.simulation.engine import _find_scenario_class

        mod = types.ModuleType("empty_mod")
        found = _find_scenario_class(mod)
        assert found is None


class TestCliJsonExitCode:
    """CLI should exit non-zero when JSON result has status=failed."""

    def test_simulate_json_failed_exits_nonzero(self) -> None:
        """Verify the _check_json_exit helper raises on failure."""
        from autocontext.cli import _check_json_exit

        with pytest.raises(SystemExit) as exc_info:
            _check_json_exit({"status": "failed", "error": "boom"})
        assert exc_info.value.code != 0

    def test_simulate_json_success_does_not_exit(self) -> None:
        from autocontext.cli import _check_json_exit

        # Should NOT raise
        _check_json_exit({"status": "completed", "score": 0.5})

    def test_simulate_json_missing_status_does_not_exit(self) -> None:
        from autocontext.cli import _check_json_exit

        # Missing status key — no crash, no exit
        _check_json_exit({"score": 0.5})
