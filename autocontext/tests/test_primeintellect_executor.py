from __future__ import annotations

import pytest

from autocontext.execution.executors.primeintellect import PrimeIntellectExecutor
from autocontext.execution.remote_execution import RemoteCleanupOutcome, RemoteExecutionResult
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
