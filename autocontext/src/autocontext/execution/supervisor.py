from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autocontext.execution.executors import ExecutionEngine, LocalExecutor
from autocontext.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)


@dataclass(slots=True)
class ExecutionInput:
    strategy: Mapping[str, object]
    seed: int
    limits: ExecutionLimits
    task_id: str | None = None
    fixture_state: Mapping[str, Any] | None = None
    fixture_observation: Observation | None = None
    fixture_digest: str | None = None

    def __post_init__(self) -> None:
        if self.task_id is not None and not self.task_id.strip():
            raise ValueError("execution task_id must be non-empty when supplied")
        fixture_fields = (
            self.fixture_state is not None,
            self.fixture_observation is not None,
            self.fixture_digest is not None,
        )
        if any(fixture_fields) and not all(fixture_fields):
            raise ValueError("prepared fixture state, observation, and digest must be supplied together")
        if self.fixture_digest is not None and (
            len(self.fixture_digest) != 64 or any(character not in "0123456789abcdef" for character in self.fixture_digest)
        ):
            raise ValueError("prepared fixture digest must be a sha256 hex digest")


@dataclass(slots=True)
class ExecutionOutput:
    result: Result
    replay: ReplayEnvelope


class ExecutionSupervisor:
    """Data-plane boundary enforcing a stable input/output contract."""

    def __init__(self, executor: ExecutionEngine | None = None) -> None:
        self.executor = executor or LocalExecutor()

    def run(self, scenario: ScenarioInterface, payload: ExecutionInput) -> ExecutionOutput:
        if payload.fixture_state is not None:
            return self._run_prepared_fixture(scenario, payload)
        execute_with_task_id = getattr(self.executor, "execute_with_task_id", None)
        if payload.task_id is not None and callable(execute_with_task_id):
            result, replay = execute_with_task_id(
                scenario=scenario,
                strategy=payload.strategy,
                seed=payload.seed,
                limits=payload.limits,
                task_id=payload.task_id,
            )
        else:
            result, replay = self.executor.execute(
                scenario=scenario,
                strategy=payload.strategy,
                seed=payload.seed,
                limits=payload.limits,
            )
        return ExecutionOutput(result=result, replay=replay)

    def _run_prepared_fixture(
        self,
        scenario: ScenarioInterface,
        payload: ExecutionInput,
    ) -> ExecutionOutput:
        from autocontext.context_bundles.runtime_evaluator import runtime_fixture_digest

        assert payload.fixture_state is not None
        assert payload.fixture_observation is not None
        assert payload.fixture_digest is not None
        actual_digest = runtime_fixture_digest(payload.fixture_state, payload.fixture_observation)
        if actual_digest != payload.fixture_digest:
            raise ValueError("prepared execution fixture digest does not match its state and observation")
        if payload.task_id is not None:
            execute = getattr(self.executor, "execute_prepared_fixture_with_task_id", None)
            if not callable(execute):
                raise RuntimeError("configured executor cannot preserve task identity for a prepared fixture")
            result, replay = execute(
                scenario=scenario,
                strategy=payload.strategy,
                seed=payload.seed,
                limits=payload.limits,
                initial_state=payload.fixture_state,
                initial_observation=payload.fixture_observation,
                fixture_digest=payload.fixture_digest,
                task_id=payload.task_id,
            )
        else:
            execute = getattr(self.executor, "execute_prepared_fixture", None)
            if not callable(execute):
                raise RuntimeError("configured executor cannot execute a prepared fixture")
            result, replay = execute(
                scenario=scenario,
                strategy=payload.strategy,
                seed=payload.seed,
                limits=payload.limits,
                initial_state=payload.fixture_state,
                initial_observation=payload.fixture_observation,
                fixture_digest=payload.fixture_digest,
            )
        return ExecutionOutput(result=result, replay=replay)
