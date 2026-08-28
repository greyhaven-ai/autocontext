"""Isolated execution boundary for generated investigation modules."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autocontext.execution.isolated_python import (
    DEFAULT_MAX_MEMORY_MB,
    DEFAULT_MAX_OUTPUT_BYTES,
    IsolatedExecutionError,
    IsolatedExecutionTimeout,
    IsolationUnavailableError,
    run_isolated_json,
)
from autocontext.scenarios.investigation import EvidenceItem
from autocontext.scenarios.simulation import Action
from autocontext.simulation.helpers import find_scenario_class


@dataclass(slots=True)
class ExecutedInvestigation:
    """Validated result returned from the generated-code child."""

    steps_executed: int
    collected_evidence: list[EvidenceItem]
    final_state: dict[str, Any]


def execute_generated_investigation(
    *,
    source: str,
    name: str,
    max_steps: int | None,
    timeout_seconds: float = 30.0,
    max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> ExecutedInvestigation:
    """Execute generated investigation code in a fresh killable child."""
    try:
        raw_result = run_isolated_json(
            lambda: _execute_in_child(source=source, name=name, max_steps=max_steps),
            timeout_seconds=timeout_seconds,
            max_memory_mb=max_memory_mb,
            max_output_bytes=max_output_bytes,
        )
    except IsolatedExecutionTimeout:
        raise RuntimeError("generated investigation execution timed out") from None
    except (IsolationUnavailableError, IsolatedExecutionError):
        raise RuntimeError("generated investigation execution failed in isolation") from None
    return _result_from_wire(raw_result)


def _execute_in_child(
    *,
    source: str,
    name: str,
    max_steps: int | None,
) -> dict[str, Any]:
    """Child-only implementation; its result must cross the JSON boundary."""
    module_name = f"autocontext._investigation_gen.{name}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    exec(source, module.__dict__)  # noqa: S102
    sys.modules[module_name] = module

    scenario_class = find_scenario_class(module)
    if scenario_class is None:
        raise ValueError("No investigation scenario class found")

    instance = scenario_class()
    state = instance.initial_state(42)
    limit = max_steps or getattr(instance, "max_steps", lambda: 8)()
    steps = 0
    while steps < limit:
        if instance.is_terminal(state):
            break
        actions = instance.get_available_actions(state)
        if not actions:
            break

        next_action: Action | None = None
        for candidate in actions:
            action = Action(name=candidate.name, parameters={})
            valid, _reason = instance.validate_action(state, action)
            if valid:
                next_action = action
                break
        if next_action is None:
            break

        action_result, state = instance.execute_action(state, next_action)
        if action_result.success:
            steps += 1
        else:
            break

    evidence_pool = {item.id: item for item in instance.get_evidence_pool(state)}
    collected_ids = [str(item) for item in state.get("collected_evidence_ids", [])]
    collected = [evidence_pool[item_id] for item_id in collected_ids if item_id in evidence_pool]
    return {
        "steps_executed": steps,
        "collected_evidence": [item.model_dump(mode="json") for item in collected],
        "final_state": state,
    }


def _result_from_wire(raw: Any) -> ExecutedInvestigation:
    """Validate and reconstruct an investigation result from child JSON."""
    if not isinstance(raw, Mapping):
        raise RuntimeError("generated investigation returned an invalid result")
    steps = raw.get("steps_executed")
    evidence_raw = raw.get("collected_evidence")
    final_state = raw.get("final_state")
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
        raise RuntimeError("generated investigation returned an invalid step count")
    if not isinstance(evidence_raw, list) or not all(isinstance(item, Mapping) for item in evidence_raw):
        raise RuntimeError("generated investigation returned invalid evidence")
    if not isinstance(final_state, dict):
        raise RuntimeError("generated investigation returned an invalid final state")
    try:
        evidence = [EvidenceItem.model_validate(dict(item)) for item in evidence_raw]
    except (TypeError, ValueError):
        raise RuntimeError("generated investigation returned invalid evidence") from None
    return ExecutedInvestigation(
        steps_executed=steps,
        collected_evidence=evidence,
        final_state=final_state,
    )
