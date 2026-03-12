"""ScenarioEvaluator — adapter bridging AutoContext ScenarioInterface to harness Evaluator protocol."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult


class ScenarioEvaluator:
    """Adapts a ScenarioInterface + ExecutionSupervisor to the Evaluator protocol.

    Uses duck typing — accepts any object with the right method signatures.
    This avoids importing AutoContext-domain types into the harness layer at module level.
    """

    def __init__(self, scenario: Any, supervisor: Any) -> None:
        self._scenario = scenario
        self._supervisor = supervisor

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        from autocontext.execution.supervisor import ExecutionInput
        from autocontext.scenarios.base import ExecutionLimits as MtsLimits

        mts_limits = MtsLimits(
            timeout_seconds=limits.timeout_seconds,
            max_memory_mb=limits.max_memory_mb,
            network_access=limits.network_access,
        )
        payload = ExecutionInput(strategy=candidate, seed=seed, limits=mts_limits)
        output = self._supervisor.run(self._scenario, payload)
        return EvaluationResult(
            score=output.result.score,
            passed=output.result.passed_validation,
            errors=list(output.result.validation_errors),
            metadata={
                "metrics": dict(output.result.metrics) if hasattr(output.result, "metrics") else {},
                "execution_output": output,
            },
            replay_data=output.replay.model_dump() if hasattr(output.replay, "model_dump") else {},
        )
