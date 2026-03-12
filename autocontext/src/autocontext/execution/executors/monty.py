"""MontyExecutor — sandboxed execution via pydantic-monty interpreter."""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result, ScenarioInterface


def _create_monty(code: str, inputs: list[str], external_functions: list[str]) -> Any:
    """Create a Monty interpreter instance. Separated for testability (mock target)."""
    try:
        import pydantic_monty
    except ImportError as exc:
        raise ImportError(
            "pydantic-monty is required for executor_mode=monty. "
            "Install with: uv sync --extra monty"
        ) from exc
    return pydantic_monty.Monty(
        code,
        inputs=inputs,
        external_functions=external_functions,
    )


_EXTERNAL_FUNCTIONS = [
    "initial_state",
    "validate_actions",
    "step",
    "is_terminal",
    "get_result",
]

_CODE_STRATEGY_EXTERNAL_FUNCTIONS = [
    "get_observation",
    "initial_state",
    "validate_actions",
    "step",
    "is_terminal",
    "get_result",
]

_EVAL_SCRIPT = """\
state = initial_state(seed)
valid_result = validate_actions(state, strategy)
valid = valid_result[0]
reason = valid_result[1]

if not valid:
    result = {
        "score": 0.0,
        "winner": "incumbent",
        "summary": "strategy rejected during validation",
        "replay": [{"event": "validation_failed", "reason": reason}],
        "metrics": {"valid": 0.0},
        "validation_errors": [reason],
    }
else:
    next_state = step(state, strategy)
    terminal = is_terminal(next_state)
    if not terminal:
        next_state["terminal"] = True
    result = get_result(next_state)

result
"""

_CODE_STRATEGY_EVAL_SCRIPT = """\
state = initial_state(seed)
observation = get_observation(state)

# --- Agent code runs here ---
{agent_code}
# --- End agent code ---

# result must be assigned by agent code
actions = result
valid_result = validate_actions(state, actions)
valid = valid_result[0]
reason = valid_result[1]

if not valid:
    result = {{
        "score": 0.0,
        "winner": "incumbent",
        "summary": "code strategy produced invalid actions",
        "replay": [{{"event": "validation_failed", "reason": reason}}],
        "metrics": {{"valid": 0.0}},
        "validation_errors": [reason],
    }}
else:
    next_state = step(state, actions)
    terminal = is_terminal(next_state)
    if not terminal:
        next_state["terminal"] = True
    result = get_result(next_state)

result
"""


class MontyExecutor:
    """Sandboxed execution engine using pydantic-monty interpreter.

    Scenario classes run on the host. Monty sandboxes a generated evaluation
    script that calls back to the host via external functions for each
    ScenarioInterface method.
    """

    def __init__(
        self,
        max_execution_time_seconds: float = 30.0,
        max_external_calls: int = 100,
    ) -> None:
        self._max_execution_time_seconds = max_execution_time_seconds
        self._max_external_calls = max_external_calls

    @staticmethod
    def build_eval_script() -> str:
        """Return the evaluation script that runs inside the Monty sandbox."""
        return _EVAL_SCRIPT

    @staticmethod
    def build_code_strategy_script(agent_code: str) -> str:
        """Build eval script that runs agent-authored code inside the sandbox."""
        return _CODE_STRATEGY_EVAL_SCRIPT.format(agent_code=agent_code)

    def _build_dispatch(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
    ) -> Any:
        """Build a dispatch function that routes external function calls to the scenario.

        Returns a callable: (function_name, args) -> return_value
        """
        def dispatch(function_name: str, args: tuple[Any, ...]) -> Any:
            if function_name == "initial_state":
                return scenario.initial_state(seed=args[0])
            elif function_name == "validate_actions":
                state, actions = args[0], args[1]
                valid, reason = scenario.validate_actions(state, "challenger", actions)
                return [valid, reason]  # list for Monty compatibility (no tuples)
            elif function_name == "step":
                state, actions = args[0], args[1]
                return dict(scenario.step(state, actions))
            elif function_name == "is_terminal":
                return scenario.is_terminal(args[0])
            elif function_name == "get_result":
                result = scenario.get_result(args[0])
                return result.model_dump()
            else:
                raise ValueError(f"Unknown external function: {function_name}")

        return dispatch

    def _build_code_dispatch(
        self,
        scenario: ScenarioInterface,
        seed: int,
    ) -> Any:
        """Build dispatch for code strategy mode (includes get_observation)."""
        def dispatch(function_name: str, args: tuple[Any, ...]) -> Any:
            if function_name == "get_observation":
                obs = scenario.get_observation(args[0], player_id="challenger")
                return {"narrative": obs.narrative, "state": dict(obs.state), "constraints": list(obs.constraints)}
            elif function_name == "initial_state":
                return scenario.initial_state(seed=args[0])
            elif function_name == "validate_actions":
                state, actions = args[0], args[1]
                valid, reason = scenario.validate_actions(state, "challenger", actions)
                return [valid, reason]
            elif function_name == "step":
                state, actions = args[0], args[1]
                return dict(scenario.step(state, actions))
            elif function_name == "is_terminal":
                return scenario.is_terminal(args[0])
            elif function_name == "get_result":
                result = scenario.get_result(args[0])
                return result.model_dump()
            else:
                raise ValueError(f"Unknown external function: {function_name}")
        return dispatch

    def execute_code_strategy(
        self,
        scenario: ScenarioInterface,
        code: str,
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        """Execute an agent-authored code strategy inside Monty sandbox."""
        script = self.build_code_strategy_script(code)
        try:
            monty = _create_monty(
                code=script,
                inputs=["seed"],
                external_functions=_CODE_STRATEGY_EXTERNAL_FUNCTIONS,
            )
        except ImportError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to create Monty interpreter for code strategy: {exc}") from exc

        dispatch = self._build_code_dispatch(scenario, seed)
        timeout = min(limits.timeout_seconds, self._max_execution_time_seconds)

        try:
            start_time = time.monotonic()
            progress = monty.start(inputs={"seed": seed})
            calls = 0

            while hasattr(progress, "function_name"):
                elapsed = time.monotonic() - start_time
                if elapsed > timeout:
                    raise TimeoutError(
                        f"Code strategy exceeded {timeout}s timeout after {calls} calls"
                    )
                calls += 1
                if calls > self._max_external_calls:
                    raise TimeoutError(
                        f"Code strategy exceeded {self._max_external_calls} external function calls"
                    )
                return_value = dispatch(progress.function_name, progress.args)
                progress = progress.resume(return_value=return_value)
        except (TimeoutError, ValueError):
            raise
        except Exception as exc:
            return Result(
                score=0.0,
                winner="incumbent",
                summary=f"code strategy execution error: {exc}",
                replay=[{"event": "code_error", "error": str(exc)}],
                metrics={},
                validation_errors=[str(exc)],
            ), ReplayEnvelope(
                scenario=scenario.name,
                seed=seed,
                narrative=f"Code strategy failed: {exc}",
                timeline=[{"event": "code_error"}],
            )

        raw_result = progress.output
        result = Result.model_validate(raw_result)
        replay = ReplayEnvelope(
            scenario=scenario.name,
            seed=seed,
            narrative=scenario.replay_to_narrative(result.replay),
            timeline=result.replay,
        )
        return result, replay

    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        if "__code__" in strategy:
            return self.execute_code_strategy(
                scenario=scenario,
                code=str(strategy["__code__"]),
                seed=seed,
                limits=limits,
            )
        script = self.build_eval_script()
        try:
            monty = _create_monty(
                code=script,
                inputs=["strategy", "seed"],
                external_functions=_EXTERNAL_FUNCTIONS,
            )
        except ImportError:
            raise  # Let import errors propagate with the helpful message
        except Exception as exc:
            raise RuntimeError(f"Failed to create Monty interpreter: {exc}") from exc

        dispatch = self._build_dispatch(scenario, strategy, seed)
        timeout = min(limits.timeout_seconds, self._max_execution_time_seconds)

        try:
            start_time = time.monotonic()
            progress = monty.start(inputs={"strategy": dict(strategy), "seed": seed})
            calls = 0

            while hasattr(progress, "function_name"):
                elapsed = time.monotonic() - start_time
                if elapsed > timeout:
                    raise TimeoutError(
                        f"Monty sandbox exceeded {timeout}s timeout "
                        f"after {calls} external function calls"
                    )
                calls += 1
                if calls > self._max_external_calls:
                    raise TimeoutError(
                        f"Monty sandbox exceeded {self._max_external_calls} external function calls"
                    )

                return_value = dispatch(progress.function_name, progress.args)
                progress = progress.resume(return_value=return_value)
        except (TimeoutError, ValueError):
            raise  # Let our own errors propagate
        except Exception as exc:
            raise RuntimeError(
                f"Monty sandbox execution failed for scenario '{scenario.name}': {exc}"
            ) from exc

        # progress is now MontyComplete — extract the result dict
        raw_result = progress.output
        result = Result.model_validate(raw_result)
        replay = ReplayEnvelope(
            scenario=scenario.name,
            seed=seed,
            narrative=scenario.replay_to_narrative(result.replay),
            timeline=result.replay,
        )
        return result, replay
