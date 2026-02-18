"""ScenarioEvaluator — adapter bridging MTS ScenarioInterface to harness Evaluator protocol."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mts.harness.evaluation.types import EvaluationLimits, EvaluationResult


class ScenarioEvaluator:
    """Adapts a ScenarioInterface + ExecutionSupervisor to the Evaluator protocol.

    Uses duck typing — accepts any object with the right method signatures.
    This avoids importing MTS-domain types into the harness layer at module level.
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
        from mts.execution.supervisor import ExecutionInput
        from mts.scenarios.base import ExecutionLimits as MtsLimits

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
            metadata=dict(output.result.metrics) if hasattr(output.result, "metrics") else {},
            replay_data=output.replay.model_dump() if hasattr(output.replay, "model_dump") else {},
        )
