from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from autocontext.execution.scenario_remote_task import build_scenario_remote_request
from autocontext.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)

if TYPE_CHECKING:
    from autocontext.integrations.primeintellect.client import PrimeIntellectClient


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
        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            task_id=None,
            initial_state=None,
            initial_observation=None,
            fixture_digest=None,
        )

    def execute_prepared_fixture(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any],
        initial_observation: Observation,
        fixture_digest: str,
    ) -> tuple[Result, ReplayEnvelope]:
        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            task_id=None,
            initial_state=initial_state,
            initial_observation=initial_observation,
            fixture_digest=fixture_digest,
        )

    def execute_with_task_id(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        task_id: str,
    ) -> tuple[Result, ReplayEnvelope]:
        """Execute using the caller's lease-unique cancellation identity."""

        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            task_id=task_id,
            initial_state=None,
            initial_observation=None,
            fixture_digest=None,
        )

    def execute_prepared_fixture_with_task_id(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any],
        initial_observation: Observation,
        fixture_digest: str,
        task_id: str,
    ) -> tuple[Result, ReplayEnvelope]:
        """Execute one attested fixture without dropping its lease identity."""

        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            task_id=task_id,
            initial_state=initial_state,
            initial_observation=initial_observation,
            fixture_digest=fixture_digest,
        )

    def _execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        task_id: str | None,
        initial_state: Mapping[str, Any] | None,
        initial_observation: Observation | None,
        fixture_digest: str | None,
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
            task_id=task_id,
            initial_state=initial_state,
            initial_observation=(initial_observation.model_dump(mode="json") if initial_observation is not None else None),
            fixture_digest=fixture_digest,
        )
        remote = self.client.execute_request(
            request,
            max_retries=self.max_retries,
            backoff_seconds=self.backoff_seconds,
        )
        if not remote.succeeded:
            if self.client.allow_fallback:
                if initial_state is not None:
                    raise RuntimeError("primeintellect prepared-fixture execution cannot use an unattested fallback")
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
        if fixture_digest is not None and execution.get("fixture_digest") != fixture_digest:
            raise RuntimeError("primeintellect result lacks the prepared fixture attestation")
        return result, replay
