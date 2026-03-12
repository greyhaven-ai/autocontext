from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autocontext.integrations.primeintellect import PrimeIntellectClient
from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result, ScenarioInterface


class PrimeIntellectExecutor:
    def __init__(
        self,
        client: PrimeIntellectClient,
        max_retries: int = 2,
        backoff_seconds: float = 0.75,
    ):
        self.client = client
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        execution = self.client.execute_strategy(
            scenario_name=scenario.name,
            strategy=dict(strategy),
            seed=seed,
            timeout_seconds=limits.timeout_seconds,
            max_memory_mb=limits.max_memory_mb,
            network_access=limits.network_access,
            max_retries=self.max_retries,
            backoff_seconds=self.backoff_seconds,
        )
        result = Result.model_validate(execution["result"])
        replay = ReplayEnvelope.model_validate(execution["replay"])
        return result, replay
