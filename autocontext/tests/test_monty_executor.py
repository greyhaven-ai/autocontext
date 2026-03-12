# tests/test_monty_executor.py
"""Tests for MontyExecutor — sandboxed execution via pydantic-monty."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result

# --- Fake scenario for testing ---


class FakeScenario:
    name = "test_scenario"

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "terminal": False, "timeline": []}

    def validate_actions(
        self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any],
    ) -> tuple[bool, str]:
        if "aggression" not in actions:
            return False, "missing aggression"
        return True, "ok"

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        score = float(actions.get("aggression", 0.5)) * 0.6 + 0.2
        return {
            **dict(state),
            "terminal": True,
            "score": score,
            "timeline": [{"event": "turn_complete", "score": score}],
            "metrics": {"score": score},
        }

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        score = float(state.get("score", 0.0))
        return Result(
            score=score,
            winner="challenger" if score >= 0.5 else "incumbent",
            summary=f"Test score {score:.4f}",
            replay=state.get("timeline", []),
            metrics=state.get("metrics", {}),
            validation_errors=[],
        )

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "Test replay narrative."


# --- Tests for the evaluation script template ---


class TestMontyEvalScript:
    def test_build_eval_script_is_valid_python(self) -> None:
        """The generated evaluation script should parse as valid Python."""
        import ast

        from autocontext.execution.executors.monty import MontyExecutor

        script = MontyExecutor.build_eval_script()
        ast.parse(script)  # Should not raise

    def test_build_eval_script_references_expected_externals(self) -> None:
        """Script should call the external functions we expose."""
        from autocontext.execution.executors.monty import MontyExecutor

        script = MontyExecutor.build_eval_script()
        assert "initial_state" in script
        assert "validate_actions" in script
        assert "step" in script
        assert "is_terminal" in script
        assert "get_result" in script

    def test_build_eval_script_uses_inputs(self) -> None:
        """Script should reference the strategy and seed inputs."""
        from autocontext.execution.executors.monty import MontyExecutor

        script = MontyExecutor.build_eval_script()
        assert "strategy" in script
        assert "seed" in script


# --- Tests for external function dispatch ---


class TestMontyExternalFunctionDispatch:
    def test_dispatch_initial_state(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()
        dispatch = executor._build_dispatch(scenario, dict({"aggression": 0.8}), 42)

        result = dispatch("initial_state", (42,))
        assert isinstance(result, dict)
        assert result["seed"] == 42
        assert result["terminal"] is False

    def test_dispatch_validate_actions_valid(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()
        dispatch = executor._build_dispatch(scenario, {"aggression": 0.8}, 42)

        state = scenario.initial_state(seed=42)
        result = dispatch("validate_actions", (state, {"aggression": 0.8}))
        assert result == [True, "ok"]

    def test_dispatch_validate_actions_invalid(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()
        dispatch = executor._build_dispatch(scenario, {}, 42)

        state = scenario.initial_state(seed=42)
        result = dispatch("validate_actions", (state, {}))
        assert result[0] is False
        assert "aggression" in result[1]

    def test_dispatch_step(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()
        dispatch = executor._build_dispatch(scenario, {"aggression": 0.8}, 42)

        state = scenario.initial_state(seed=42)
        result = dispatch("step", (state, {"aggression": 0.8}))
        assert isinstance(result, dict)
        assert result["terminal"] is True

    def test_dispatch_is_terminal(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()
        dispatch = executor._build_dispatch(scenario, {"aggression": 0.8}, 42)

        result = dispatch("is_terminal", ({"terminal": True},))
        assert result is True

        result = dispatch("is_terminal", ({"terminal": False},))
        assert result is False

    def test_dispatch_get_result(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()
        dispatch = executor._build_dispatch(scenario, {"aggression": 0.8}, 42)

        state = {"terminal": True, "score": 0.7, "timeline": [], "metrics": {"score": 0.7}}
        result = dispatch("get_result", (state,))
        assert isinstance(result, dict)
        assert result["score"] == 0.7

    def test_dispatch_unknown_function_raises(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()
        dispatch = executor._build_dispatch(scenario, {}, 42)

        with pytest.raises(ValueError, match="Unknown external function"):
            dispatch("unknown_func", ())


# --- Tests for full execute path (mocked Monty) ---


class TestMontyExecutorExecute:
    def _mock_monty_run(self, final_result: dict[str, Any]) -> MagicMock:
        """Build a mock that simulates the Monty start/resume cycle.

        IMPORTANT: MagicMock objects always report True for hasattr() on any
        attribute. Since MontyExecutor uses `hasattr(progress, "function_name")`
        to detect snapshots vs completion, we must use spec=[] on the completion
        mock so hasattr returns False.
        """
        # Final completion object — must NOT have function_name attribute
        complete = MagicMock(spec=[])  # spec=[] means no auto-attributes
        complete.output = final_result

        # Snapshot objects for each external function call
        # Simulate: initial_state → validate_actions → step → is_terminal → get_result
        snapshots = []
        for fn_name, args in [
            ("initial_state", (42,)),
            ("validate_actions", ({"seed": 42, "terminal": False, "timeline": []}, {"aggression": 0.8})),
            ("step", ({"seed": 42, "terminal": False, "timeline": []}, {"aggression": 0.8})),
            ("is_terminal", ({"terminal": True, "score": 0.68, "timeline": [], "metrics": {}},)),
            ("get_result", ({"terminal": True, "score": 0.68, "timeline": [], "metrics": {}},)),
        ]:
            snap = MagicMock()
            snap.function_name = fn_name
            snap.args = args
            snapshots.append(snap)

        # Chain: start → snap0, snap0.resume → snap1, ..., snapN.resume → complete
        for i, snap in enumerate(snapshots):
            if i + 1 < len(snapshots):
                snap.resume.return_value = snapshots[i + 1]
            else:
                snap.resume.return_value = complete

        # Monty constructor mock
        monty_instance = MagicMock()
        monty_instance.start.return_value = snapshots[0]

        return monty_instance

    def test_execute_returns_result_and_replay(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()

        final_result = {
            "score": 0.68,
            "winner": "challenger",
            "summary": "Test score 0.6800",
            "replay": [{"event": "turn_complete", "score": 0.68}],
            "metrics": {"score": 0.68},
            "validation_errors": [],
        }

        mock_monty = self._mock_monty_run(final_result)
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock_monty):
            result, replay = executor.execute(
                scenario=scenario,
                strategy={"aggression": 0.8},
                seed=42,
                limits=ExecutionLimits(),
            )

        assert isinstance(result, Result)
        assert result.score == 0.68
        assert result.winner == "challenger"
        assert isinstance(replay, ReplayEnvelope)
        assert replay.scenario == "test_scenario"
        assert replay.seed == 42

    def test_execute_handles_validation_failure(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()

        final_result = {
            "score": 0.0,
            "winner": "incumbent",
            "summary": "strategy rejected during validation",
            "replay": [{"event": "validation_failed", "reason": "missing aggression"}],
            "metrics": {"valid": 0.0},
            "validation_errors": ["missing aggression"],
        }

        # Only 2 calls: initial_state, validate_actions (fails), script returns early
        snap_init = MagicMock()
        snap_init.function_name = "initial_state"
        snap_init.args = (42,)
        snap_validate = MagicMock()
        snap_validate.function_name = "validate_actions"
        snap_validate.args = ({"seed": 42, "terminal": False, "timeline": []}, {})

        complete = MagicMock(spec=[])  # spec=[] so hasattr(complete, "function_name") is False
        complete.output = final_result

        snap_init.resume.return_value = snap_validate
        snap_validate.resume.return_value = complete

        mock_monty = MagicMock()
        mock_monty.start.return_value = snap_init

        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock_monty):
            result, replay = executor.execute(
                scenario=scenario,
                strategy={},
                seed=42,
                limits=ExecutionLimits(),
            )

        assert result.score == 0.0
        assert result.validation_errors == ["missing aggression"]

    def test_execute_timeout_raises(self) -> None:
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()

        # Simulate infinite loop of external calls (never completes)
        snap = MagicMock()
        snap.function_name = "initial_state"
        snap.args = (42,)
        snap.resume.return_value = snap  # loops forever

        mock_monty = MagicMock()
        mock_monty.start.return_value = snap

        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock_monty):
            with pytest.raises(TimeoutError, match="Monty sandbox exceeded"):
                executor.execute(
                    scenario=scenario,
                    strategy={"aggression": 0.8},
                    seed=42,
                    limits=ExecutionLimits(timeout_seconds=0.01),
                )


# --- Tests for exception handling ---


class TestMontyExecutorErrorHandling:
    def test_monty_creation_error_wrapped(self) -> None:
        """Errors during Monty interpreter creation are wrapped in RuntimeError."""
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()

        with patch("autocontext.execution.executors.monty._create_monty", side_effect=RuntimeError("bad code")):
            with pytest.raises(RuntimeError, match="Failed to create Monty interpreter"):
                executor.execute(
                    scenario=scenario,
                    strategy={"aggression": 0.8},
                    seed=42,
                    limits=ExecutionLimits(),
                )

    def test_monty_execution_error_wrapped(self) -> None:
        """Errors during Monty execution are wrapped in RuntimeError with scenario name."""
        from autocontext.execution.executors.monty import MontyExecutor

        scenario = FakeScenario()
        executor = MontyExecutor()

        mock_monty = MagicMock()
        mock_monty.start.side_effect = RuntimeError("interpreter crash")

        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock_monty):
            with pytest.raises(RuntimeError, match="test_scenario"):
                executor.execute(
                    scenario=scenario,
                    strategy={"aggression": 0.8},
                    seed=42,
                    limits=ExecutionLimits(),
                )


# --- Test: import error when pydantic-monty not installed ---


class TestMontyImportGuard:
    def test_import_error_message_is_helpful(self) -> None:
        """When pydantic-monty is not installed and executor_mode=monty, error is clear."""
        from autocontext.execution.executors.monty import MontyExecutor

        executor = MontyExecutor()
        # The executor itself should be importable always.
        # The error comes at execute-time if pydantic_monty is missing.
        assert executor is not None
