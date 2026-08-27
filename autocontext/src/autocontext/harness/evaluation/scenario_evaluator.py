"""ScenarioEvaluator — adapter bridging autocontext ScenarioInterface to harness Evaluator protocol."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from autocontext.extensions import HookBus, active_hook_bus
from autocontext.harness.evaluation.dimensional import (
    extract_dimension_scores,
    normalize_dimension_specs,
)
from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult


def generation_evaluation_namespace(run_id: str, generation: int, phase: str) -> str:
    """Return a restart-stable namespace for one generation evaluation phase.

    ``run_id`` and ``generation`` are the durable coordinates in the generation
    journal. Callers supply a deterministic phase (including an application
    retry index where relevant), so reconstructing the same journaled work after
    restart reproduces its identities without a process-local counter.
    """
    clean_run_id = run_id.strip()
    clean_phase = phase.strip()
    if not clean_run_id:
        raise ValueError("generation evaluation run_id must be non-empty")
    if generation < 0:
        raise ValueError("generation evaluation index must be non-negative")
    if not clean_phase:
        raise ValueError("generation evaluation phase must be non-empty")
    return f"run:{clean_run_id}:generation:{generation}:evaluation:{clean_phase}"


class ScenarioEvaluator:
    """Adapts a ScenarioInterface + ExecutionSupervisor to the Evaluator protocol.

    Uses duck typing — accepts any object with the right method signatures.
    This avoids importing autocontext-domain types into the harness layer at module level.
    A supplied ``task_namespace`` produces deterministic paid-execution task
    identities bound to the candidate, seed, and prepared fixture. Strict task
    identity instead binds the semantic namespace, seed, and fixture while the
    durable outbox rejects regenerated candidate bytes under that same task.
    """

    def __init__(
        self,
        scenario: Any,
        supervisor: Any,
        hook_bus: HookBus | None = None,
        *,
        task_namespace: str | None = None,
        strict_task_identity: bool = False,
    ) -> None:
        self._scenario = scenario
        self._supervisor = supervisor
        self._hook_bus = hook_bus
        self._task_namespace = task_namespace.strip() if task_namespace is not None else None
        if task_namespace is not None and not self._task_namespace:
            raise ValueError("evaluation task_namespace must be non-empty when supplied")
        if not isinstance(strict_task_identity, bool):
            raise TypeError("evaluation strict_task_identity must be boolean")
        if strict_task_identity and self._task_namespace is None:
            raise ValueError("strict task identity requires an evaluation task_namespace")
        self._strict_task_identity = strict_task_identity

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
        *,
        fixture_state: Mapping[str, Any] | None = None,
        fixture_observation: Any | None = None,
        fixture_digest: str | None = None,
    ) -> EvaluationResult:
        from autocontext.execution.supervisor import ExecutionInput
        from autocontext.scenarios.base import ExecutionLimits as MtsLimits

        mts_limits = MtsLimits(
            timeout_seconds=limits.timeout_seconds,
            max_memory_mb=limits.max_memory_mb,
            network_access=limits.network_access,
        )
        candidate_snapshot = _snapshot_candidate(candidate)
        payload = ExecutionInput(
            strategy=candidate_snapshot,
            seed=seed,
            limits=mts_limits,
            task_id=self._task_id(candidate_snapshot, seed, fixture_digest),
            strict_task_identity=self._strict_task_identity,
            fixture_state=fixture_state,
            fixture_observation=fixture_observation,
            fixture_digest=fixture_digest,
        )
        with active_hook_bus(self._hook_bus):
            output = self._supervisor.run(self._scenario, payload)
        metrics = dict(output.result.metrics) if hasattr(output.result, "metrics") else {}
        raw_dimension_specs = self._scenario.scoring_dimensions() if hasattr(self._scenario, "scoring_dimensions") else None
        dimension_specs = normalize_dimension_specs(
            raw_dimension_specs if isinstance(raw_dimension_specs, list) else None,
        )
        dimension_scores = extract_dimension_scores(metrics, dimension_specs)
        return EvaluationResult(
            score=output.result.score,
            passed=output.result.passed_validation,
            errors=list(output.result.validation_errors),
            metadata={
                "metrics": metrics,
                "dimension_specs": [spec.to_dict() for spec in dimension_specs],
                "execution_output": output,
            },
            replay_data=output.replay.model_dump() if hasattr(output.replay, "model_dump") else {},
            dimension_scores=dimension_scores,
        )

    def _task_id(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        fixture_digest: str | None,
    ) -> str | None:
        if self._task_namespace is None:
            return None
        identity_payload: dict[str, Any] = {
            "fixture_digest": fixture_digest,
            "seed": seed,
        }
        if not self._strict_task_identity:
            identity_payload["candidate"] = dict(candidate)
        identity = json.dumps(
            identity_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(identity).hexdigest()
        return f"{self._task_namespace}:trial:{digest}"


def _snapshot_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Detach one canonical JSON candidate for both identity and execution."""

    if not isinstance(candidate, Mapping):
        raise TypeError("evaluation candidate must be a mapping")
    try:
        encoded = json.dumps(
            dict(candidate),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        snapshot = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise TypeError("evaluation candidate must be a finite JSON object") from exc
    if type(snapshot) is not dict:
        raise TypeError("evaluation candidate must be a JSON object")
    return snapshot


__all__ = ["ScenarioEvaluator", "generation_evaluation_namespace"]
