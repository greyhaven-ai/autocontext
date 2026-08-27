"""Isolated execution boundary for generated simulation modules."""

from __future__ import annotations

import importlib.util
import logging
import math
import sys
from collections.abc import Callable, Mapping
from typing import Any

from autocontext.execution.isolated_python import (
    DEFAULT_MAX_MEMORY_MB,
    DEFAULT_MAX_OUTPUT_BYTES,
    IsolatedExecutionError,
    IsolatedExecutionTimeout,
    IsolationUnavailableError,
    run_isolated_json,
)
from autocontext.simulation.helpers import find_scenario_class

logger = logging.getLogger(__name__)

DEFAULT_SIMULATION_TIMEOUT_SECONDS = 30.0


def execute_generated_simulation(
    *,
    source: str,
    name: str,
    seed: int,
    max_steps: int | None,
    execute_operator_loop: Callable[[Any, int, int | None], dict[str, Any]],
    timeout_seconds: float = DEFAULT_SIMULATION_TIMEOUT_SECONDS,
    max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Run a generated simulation only inside a killable child."""
    try:
        raw_result = run_isolated_json(
            lambda: _execute_source_in_child(
                source=source,
                name=name,
                seed=seed,
                max_steps=max_steps,
                execute_operator_loop=execute_operator_loop,
            ),
            timeout_seconds=timeout_seconds,
            max_memory_mb=max_memory_mb,
            max_output_bytes=max_output_bytes,
        )
        return _result_from_wire(raw_result)
    except IsolatedExecutionTimeout:
        return _failed_result("generated simulation execution timed out")
    except (IsolationUnavailableError, IsolatedExecutionError, ValueError):
        logger.debug("simulation.generated_execution: isolated execution failed", exc_info=True)
        return _failed_result("generated simulation execution failed in isolation")


def _failed_result(reasoning: str) -> dict[str, Any]:
    return {"score": 0, "reasoning": reasoning, "dimension_scores": {}}


def _result_from_wire(raw: Any) -> dict[str, Any]:
    """Validate the JSON-only response produced by a generated scenario."""
    if not isinstance(raw, Mapping):
        raise ValueError("simulation child result must be an object")
    score = raw.get("score")
    reasoning = raw.get("reasoning")
    dimensions = raw.get("dimension_scores")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError("simulation child returned an invalid score")
    if not isinstance(reasoning, str) or not isinstance(dimensions, Mapping):
        raise ValueError("simulation child returned invalid result fields")
    validated: dict[str, float] = {}
    for key, value in dimensions.items():
        if not isinstance(key, str):
            raise ValueError("simulation child returned a non-string dimension name")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("simulation child returned an invalid dimension score")
        validated[key] = float(value)
    result: dict[str, Any] = {
        "score": float(score),
        "reasoning": reasoning,
        "dimension_scores": validated,
    }
    for signal_name in ("escalation_count", "clarification_count"):
        signal_value = raw.get(signal_name)
        if signal_value is None:
            continue
        if isinstance(signal_value, bool) or not isinstance(signal_value, int) or signal_value < 0:
            raise ValueError(f"simulation child returned an invalid {signal_name}")
        result[signal_name] = signal_value
    return result


def _execute_source_in_child(
    *,
    source: str,
    name: str,
    seed: int,
    max_steps: int | None,
    execute_operator_loop: Callable[[Any, int, int | None], dict[str, Any]],
) -> dict[str, Any]:
    """Load and run one generated scenario in the isolated child process."""
    module_name = f"autocontext._sim_gen.{name}_{seed}"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    exec(source, module.__dict__)  # noqa: S102
    sys.modules[module_name] = module

    scenario_class = find_scenario_class(module)
    if scenario_class is None:
        return _failed_result("No scenario class found")
    instance = scenario_class()

    from autocontext.scenarios.operator_loop import OperatorLoopInterface

    if isinstance(instance, OperatorLoopInterface):
        return execute_operator_loop(instance, seed, max_steps)

    from autocontext.scenarios.simulation import Action, ActionRecord, ActionResult, ActionTrace

    state = instance.initial_state(seed)
    limit = max_steps or getattr(instance, "max_steps", lambda: 20)()
    records: list[dict[str, Any]] = []
    for step_num in range(1, limit + 1):
        if instance.is_terminal(state):
            break
        actions = instance.get_available_actions(state)
        if not actions:
            break
        action = Action(name=actions[0].name, parameters={})
        state_before = dict(state)
        action_result, state = instance.execute_action(state, action)
        records.append({
            "step": step_num,
            "action": action.name,
            "success": action_result.success,
            "state_before": state_before,
            "state_after": dict(state),
        })

    trace = ActionTrace(records=[
        ActionRecord(
            step=record["step"],
            action=Action(name=record["action"], parameters={}),
            result=ActionResult(success=record["success"], output="", state_changes={}),
            state_before=record["state_before"],
            state_after=record["state_after"],
        )
        for record in records
    ])
    evaluation = instance.evaluate_trace(trace, state)
    return {
        "score": round(evaluation.score, 4),
        "reasoning": evaluation.reasoning,
        "dimension_scores": evaluation.dimension_scores,
    }
