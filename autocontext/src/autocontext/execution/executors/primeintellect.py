from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from autocontext.execution.scenario_remote_task import build_scenario_remote_request
from autocontext.integrations.primeintellect import PrimeIntellectClient
from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result, ScenarioInterface


class PrimeIntellectExecutor:
    def __init__(
        self,
        client: PrimeIntellectClient,
        max_retries: int = 2,
        backoff_seconds: float = 0.75,
    ) -> None:
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
        request = build_scenario_remote_request(
            scenario,
            strategy,
            seed,
            limits,
            image=self.client.docker_image,
            cpu_cores=self.client.cpu_cores,
            disk_gb=self.client.disk_size_gb,
            memory_gb=self.client.memory_gb,
        )
        remote = self.client.execute_request(
            request,
            max_retries=self.max_retries,
            backoff_seconds=self.backoff_seconds,
        )
        if not remote.succeeded:
            if self.client.allow_fallback:
                fallback = self.client.fallback_local_response(scenario.name, seed)
                return Result.model_validate(fallback["result"]), ReplayEnvelope.model_validate(fallback["replay"])
            raise RuntimeError(f"primeintellect {remote.status}: {remote.error}")
        execution: dict[str, Any] = {}
        for line in reversed(remote.stdout.splitlines()):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                execution = parsed
                break
        result = Result.model_validate(execution.get("result"))
        replay = ReplayEnvelope.model_validate(execution.get("replay"))
        return result, replay
