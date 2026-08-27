from __future__ import annotations

import io
import json
import zipfile

import pytest

from autocontext.context_bundles.runtime_evaluator import runtime_fixture_digest
from autocontext.execution import RemoteExecutionFailure
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

    with pytest.raises(RemoteExecutionFailure, match="primeintellect provider_error: provider unavailable") as raised:
        PrimeIntellectExecutor(client, max_retries=0).execute(
            GridCtfScenario(),
            {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            7,
            ExecutionLimits(),
        )

    assert raised.value.result.status == "provider_error"
    assert raised.value.result.error == "provider unavailable"
    assert raised.value.retryable is False


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
            strict_task_identity=task_id.endswith("lease-1"),
        )

    assert [request.task_id for request in captured] == [
        "campaign-job-a:lease-1",
        "campaign-job-b:lease-2",
    ]
    assert [request.strict_task_identity for request in captured] == [True, False]
    assert executor.take_remote_result("campaign-job-a:lease-1") is None
    assert executor.take_remote_result("campaign-job-b:lease-2") is None


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
            strict_task_identity=True,
        )

    assert captured[0].task_id == "campaign-job:lease-7"
    assert captured[0].strict_task_identity is True
    with zipfile.ZipFile(io.BytesIO(captured[0].input_artifacts[0].content)) as archive:
        payload = json.loads(archive.read("autocontext-payload.json"))
    assert payload["initial_state"] == prepared_state
    assert payload["initial_observation"] == prepared_observation.model_dump(mode="json")
    assert payload["fixture_digest"] == fixture_digest
    assert captured[0].metadata["fixture_digest"] == fixture_digest


def test_only_campaign_execution_captures_remote_result_for_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=False)
    returned: list[RemoteExecutionResult] = []

    def execute_request(
        _client: PrimeIntellectClient,
        request: RemoteExecutionRequest,
        **_kwargs: object,
    ) -> RemoteExecutionResult:
        result = RemoteExecutionResult(
            task_id=request.task_id,
            provider="primeintellect",
            status="cleanup_error",
            cleanup=RemoteCleanupOutcome(attempted=False, succeeded=False),
            error="ambiguous paid outcome",
        )
        returned.append(result)
        return result

    monkeypatch.setattr(PrimeIntellectClient, "execute_request", execute_request)
    executor = PrimeIntellectExecutor(client, max_retries=0)
    scenario = GridCtfScenario()
    prepared_state = scenario.initial_state(seed=123)
    prepared_observation = scenario.get_observation(prepared_state, "challenger")
    fixture_digest = runtime_fixture_digest(prepared_state, prepared_observation)

    with pytest.raises(RemoteExecutionFailure, match="ambiguous paid outcome"):
        executor.execute_prepared_fixture_with_task_id(
            scenario,
            {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            123,
            ExecutionLimits(),
            initial_state=prepared_state,
            initial_observation=prepared_observation,
            fixture_digest=fixture_digest,
            task_id="ordinary-evaluator",
        )
    assert executor.take_remote_result("ordinary-evaluator") is None

    with pytest.raises(RemoteExecutionFailure, match="ambiguous paid outcome"):
        executor.execute_prepared_fixture_with_task_id_and_remote_requirements(
            scenario,
            {"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            123,
            ExecutionLimits(),
            initial_state=prepared_state,
            initial_observation=prepared_observation,
            fixture_digest=fixture_digest,
            task_id="campaign-job:lease-7",
            remote_requirements=client.default_requirements,
            strict_task_identity=True,
        )

    assert returned[-1].task_id == "campaign-job:lease-7"
    assert executor.take_remote_result("campaign-job:lease-7") is returned[-1]
    assert executor.take_remote_result("campaign-job:lease-7") is None
