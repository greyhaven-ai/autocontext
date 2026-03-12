from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autocontext.execution.executors import ExecutionEngine, LocalExecutor
from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result, ScenarioInterface


@dataclass(slots=True)
class ExecutionInput:
    strategy: Mapping[str, object]
    seed: int
    limits: ExecutionLimits


@dataclass(slots=True)
class ExecutionOutput:
    result: Result
    replay: ReplayEnvelope


class ExecutionSupervisor:
    """Data-plane boundary enforcing a stable input/output contract."""

    def __init__(self, executor: ExecutionEngine | None = None):
        self.executor = executor or LocalExecutor()

    def run(self, scenario: ScenarioInterface, payload: ExecutionInput) -> ExecutionOutput:
        result, replay = self.executor.execute(
            scenario=scenario,
            strategy=payload.strategy,
            seed=payload.seed,
            limits=payload.limits,
        )
        return ExecutionOutput(result=result, replay=replay)
