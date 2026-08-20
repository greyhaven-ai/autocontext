from __future__ import annotations

import io
import json
import zipfile

import pytest

from autocontext.context_bundles.runtime_evaluator import runtime_fixture_digest
from autocontext.execution.executors.primeintellect import PrimeIntellectExecutor
from autocontext.execution.remote_execution import RemoteCleanupOutcome, RemoteExecutionRequest, RemoteExecutionResult
from autocontext.integrations.primeintellect import PrimeIntellectClient
from autocontext.scenarios.base import ExecutionLimits
from autocontext.scenarios.grid_ctf.scenario import GridCtfScenario


def test_live_executor_honors_enabled_provider_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=True)
    monkeypatch.setattr(
        PrimeIntellectClient,
        "execute_request",
        lambda *args, **kwargs: RemoteExecutionResult(
            task_id="scenario:grid_ctf:7",
            provider="primeintellect",
            status="provider_error",
            cleanup=RemoteCleanupOutcome(attempted=False, succeeded=False),
            error="provider unavailable",
        ),
    )

    result, replay = PrimeIntellectExecutor(client, max_retries=0).execute(
        GridCtfScenario(),
        {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
        7,
        ExecutionLimits(),
    )

    assert result.score == 0.0
    assert result.validation_errors == ["remote execution unavailable"]
    assert replay.timeline == [{"event": "remote_unavailable"}]


def test_live_executor_raises_typed_failure_when_fallback_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=False)
    monkeypatch.setattr(
        PrimeIntellectClient,
        "execute_request",
        lambda *args, **kwargs: RemoteExecutionResult(
            task_id="scenario:grid_ctf:7",
            provider="primeintellect",
            status="provider_error",
            cleanup=RemoteCleanupOutcome(attempted=False, succeeded=False),
            error="provider unavailable",
        ),
    )

    with pytest.raises(RuntimeError, match="primeintellect provider_error: provider unavailable"):
        PrimeIntellectExecutor(client, max_retries=0).execute(
            GridCtfScenario(),
            {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            7,
            ExecutionLimits(),
        )


def test_live_executor_preserves_explicit_task_ids_for_duplicate_seed_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=True)
    captured: list[RemoteExecutionRequest] = []

    def execute_request(
        _client: PrimeIntellectClient,
        request: RemoteExecutionRequest,
        **_kwargs: object,
    ) -> RemoteExecutionResult:
        captured.append(request)
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider="primeintellect",
            status="provider_error",
            cleanup=RemoteCleanupOutcome(attempted=False, succeeded=False),
            error="provider unavailable",
        )

    monkeypatch.setattr(PrimeIntellectClient, "execute_request", execute_request)
    executor = PrimeIntellectExecutor(client, max_retries=0)
    for task_id in ("campaign-job-a:lease-1", "campaign-job-b:lease-2"):
        executor.execute_with_task_id(
            GridCtfScenario(),
            {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            7,
            ExecutionLimits(),
            task_id=task_id,
        )

    assert [request.task_id for request in captured] == [
        "campaign-job-a:lease-1",
        "campaign-job-b:lease-2",
    ]


def test_prepared_fixture_is_packaged_and_preserves_prime_task_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=True)
    captured: list[RemoteExecutionRequest] = []

    def execute_request(
        _client: PrimeIntellectClient,
        request: RemoteExecutionRequest,
        **_kwargs: object,
    ) -> RemoteExecutionResult:
        captured.append(request)
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider="primeintellect",
            status="provider_error",
            cleanup=RemoteCleanupOutcome(attempted=False, succeeded=False),
            error="provider unavailable",
        )

    monkeypatch.setattr(PrimeIntellectClient, "execute_request", execute_request)
    scenario = GridCtfScenario()
    prepared_state = scenario.initial_state(seed=123)
    prepared_observation = scenario.get_observation(prepared_state, "challenger")
    fixture_digest = runtime_fixture_digest(prepared_state, prepared_observation)
    with pytest.raises(RuntimeError, match="unattested fallback"):
        PrimeIntellectExecutor(client, max_retries=0).execute_prepared_fixture_with_task_id(
            GridCtfScenario(),
            {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            999,
            ExecutionLimits(),
            initial_state=prepared_state,
            initial_observation=prepared_observation,
            fixture_digest=fixture_digest,
            task_id="campaign-job:lease-7",
        )

    assert captured[0].task_id == "campaign-job:lease-7"
    with zipfile.ZipFile(io.BytesIO(captured[0].input_artifacts[0].content)) as archive:
        payload = json.loads(archive.read("autocontext-payload.json"))
    assert payload["initial_state"] == prepared_state
    assert payload["initial_observation"] == prepared_observation.model_dump(mode="json")
    assert payload["fixture_digest"] == fixture_digest
    assert captured[0].metadata["fixture_digest"] == fixture_digest
