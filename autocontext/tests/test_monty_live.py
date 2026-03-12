"""Live integration tests using real pydantic-monty. Skipped if not installed."""
from __future__ import annotations

from typing import Any

import pytest

try:
    import pydantic_monty

    HAS_MONTY = True
except ImportError:
    HAS_MONTY = False

pytestmark = pytest.mark.monty


@pytest.mark.skipif(not HAS_MONTY, reason="pydantic-monty not installed")
class TestMontyLiveBasic:
    def test_simple_expression(self) -> None:
        m = pydantic_monty.Monty("x + 1", inputs=["x"])
        result = m.run(inputs={"x": 41})
        assert result == 42

    def test_external_function_pause_resume(self) -> None:
        m = pydantic_monty.Monty(
            "result = double(value)\nresult",
            inputs=["value"],
            external_functions=["double"],
        )
        progress = m.start(inputs={"value": 21})
        assert progress.function_name == "double"
        assert progress.args == (21,)

        result = progress.resume(return_value=42)
        assert result.output == 42

    def test_dict_input_and_output(self) -> None:
        code = """\
score = strategy["aggression"] * 0.6 + strategy["defense"] * 0.4
{"score": score, "winner": "challenger" if score >= 0.5 else "incumbent"}
"""
        m = pydantic_monty.Monty(code, inputs=["strategy"])
        result = m.run(inputs={"strategy": {"aggression": 0.8, "defense": 0.5}})
        assert result["score"] == pytest.approx(0.68)
        assert result["winner"] == "challenger"


@pytest.mark.skipif(not HAS_MONTY, reason="pydantic-monty not installed")
class TestMontyLiveEvalScript:
    def test_eval_script_runs_with_real_monty(self) -> None:
        """The actual eval script template runs in real Monty."""
        from autocontext.execution.executors.monty import _EXTERNAL_FUNCTIONS, MontyExecutor

        script = MontyExecutor.build_eval_script()
        m = pydantic_monty.Monty(
            script,
            inputs=["strategy", "seed"],
            external_functions=_EXTERNAL_FUNCTIONS,
        )

        # Start and drive through external function calls manually
        progress = m.start(inputs={"strategy": {"aggression": 0.8}, "seed": 42})
        assert progress.function_name == "initial_state"

        # Provide initial state
        state = {"seed": 42, "terminal": False, "timeline": []}
        progress = progress.resume(return_value=state)
        assert progress.function_name == "validate_actions"

        # Validate actions — return success
        progress = progress.resume(return_value=[True, "ok"])
        assert progress.function_name == "step"

        # Step — return terminal state with score
        next_state = {**state, "terminal": True, "score": 0.7, "timeline": [{"event": "done"}], "metrics": {}}
        progress = progress.resume(return_value=next_state)
        assert progress.function_name == "is_terminal"

        # Is terminal
        progress = progress.resume(return_value=True)
        assert progress.function_name == "get_result"

        # Get result
        result_dict = {
            "score": 0.7, "winner": "challenger", "summary": "Test",
            "replay": [{"event": "done"}], "metrics": {}, "validation_errors": [],
        }
        final = progress.resume(return_value=result_dict)
        assert final.output["score"] == 0.7

    def test_eval_script_validation_failure_path(self) -> None:
        """Eval script handles validation failure correctly in real Monty."""
        from autocontext.execution.executors.monty import _EXTERNAL_FUNCTIONS, MontyExecutor

        script = MontyExecutor.build_eval_script()
        m = pydantic_monty.Monty(
            script,
            inputs=["strategy", "seed"],
            external_functions=_EXTERNAL_FUNCTIONS,
        )

        progress = m.start(inputs={"strategy": {}, "seed": 1})
        assert progress.function_name == "initial_state"

        progress = progress.resume(return_value={"seed": 1, "terminal": False, "timeline": []})
        assert progress.function_name == "validate_actions"

        # Return validation failure
        final = progress.resume(return_value=[False, "missing field"])
        # Script should return the failure result dict directly (no more external calls)
        assert final.output["score"] == 0.0
        assert final.output["validation_errors"] == ["missing field"]


@pytest.mark.skipif(not HAS_MONTY, reason="pydantic-monty not installed")
class TestMontyLiveFullExecutor:
    def test_full_execute_with_real_monty_and_scenario(self) -> None:
        """MontyExecutor.execute() works with a real Monty and fake scenario."""
        from autocontext.execution.executors.monty import MontyExecutor
        from autocontext.scenarios.base import ExecutionLimits, Result

        class SimpleScenario:
            name = "simple"

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"seed": seed or 0, "terminal": False, "timeline": []}

            def validate_actions(self, state: Any, player_id: str, actions: Any) -> tuple[bool, str]:
                return (True, "ok") if "x" in dict(actions) else (False, "missing x")

            def step(self, state: Any, actions: Any) -> dict[str, Any]:
                x = float(dict(actions)["x"])
                return {**dict(state), "terminal": True, "score": x, "timeline": [], "metrics": {"x": x}}

            def is_terminal(self, state: Any) -> bool:
                return bool(dict(state).get("terminal"))

            def get_result(self, state: Any) -> Result:
                s = dict(state)
                score = float(s.get("score", 0))
                return Result(
                    score=score, winner="challenger" if score > 0.5 else "incumbent",
                    summary=f"score={score}", replay=[], metrics=s.get("metrics", {}),
                    validation_errors=[],
                )

            def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
                return "done"

        executor = MontyExecutor()
        result, replay = executor.execute(
            scenario=SimpleScenario(),
            strategy={"x": 0.75},
            seed=42,
            limits=ExecutionLimits(timeout_seconds=10.0),
        )
        assert result.score == pytest.approx(0.75)
        assert result.winner == "challenger"
        assert replay.scenario == "simple"
