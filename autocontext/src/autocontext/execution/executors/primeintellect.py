from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from autocontext.execution.remote_execution import RemoteExecutionRequirements, RemoteExecutionResult
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
        self._remote_results: dict[str, RemoteExecutionResult] = {}
        self._remote_results_lock = threading.RLock()

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
            remote_requirements=None,
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
            remote_requirements=None,
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
            remote_requirements=None,
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
            remote_requirements=None,
        )

    def execute_prepared_fixture_with_task_id_and_remote_requirements(
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
        remote_requirements: RemoteExecutionRequirements,
    ) -> tuple[Result, ReplayEnvelope]:
        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            task_id=task_id,
            initial_state=initial_state,
            initial_observation=initial_observation,
            fixture_digest=fixture_digest,
            remote_requirements=remote_requirements,
        )

    def take_remote_result(self, task_id: str) -> RemoteExecutionResult | None:
        with self._remote_results_lock:
            return self._remote_results.pop(task_id, None)

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
        remote_requirements: RemoteExecutionRequirements | None,
    ) -> tuple[Result, ReplayEnvelope]:
        requirements = remote_requirements or self.client.default_requirements
        if requirements is None:
            raise RuntimeError("Prime Intellect execution requirements are unavailable")
        request = build_scenario_remote_request(
            scenario,
            strategy,
            seed,
            limits,
            image=requirements.image,
            cpu_cores=requirements.resources.cpu_cores,
            disk_gb=requirements.resources.disk_gb,
            memory_gb=requirements.resources.memory_gb,
            accelerator=requirements.resources.accelerator,
            region=requirements.region,
            required_telemetry=requirements.required_telemetry,
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
        if task_id is not None:
            with self._remote_results_lock:
                self._remote_results[task_id] = remote
        if not remote.succeeded:
            if self.client.allow_fallback:
                if requirements.resources.accelerator is not None:
                    raise RuntimeError(
                        f"primeintellect {remote.status}: accelerator execution cannot fall back to CPU: {remote.error}"
                    )
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
