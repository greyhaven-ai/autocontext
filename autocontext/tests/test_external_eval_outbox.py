from __future__ import annotations

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autocontext.config.settings import AppSettings
from autocontext.execution.external_eval_outbox import (
    ExternalEvalLedgerOutbox,
    ExternalEvalOutboxConflictError,
    ExternalEvalOutboxPendingError,
    external_eval_attempt_id,
)
from autocontext.execution.remote_execution import (
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteOutputArtifact,
    RemoteResolvedEnvironment,
    RemoteResourceUsage,
    remote_request_provenance,
)
from autocontext.execution.runtime_factory import build_execution_runtime
from autocontext.integrations.primeintellect.client import PrimeIntellectClient


def _request(task_id: str = "paid-eval") -> RemoteExecutionRequest:
    return RemoteExecutionRequest(
        task_id=task_id,
        image="python:3.13",
        command="python task.py",
        metadata={"campaign": "release-test"},
    )


def _result(request: RemoteExecutionRequest) -> RemoteExecutionResult:
    return RemoteExecutionResult(
        task_id=request.task_id,
        provider="primeintellect",
        status="success",
        stdout='{"result":"ok"}',
        exit_code=0,
        artifacts=(RemoteOutputArtifact("report.json", b'{"score": 1}', "application/json"),),
        events=(
            RemoteExecutionEvent(
                sequence=1,
                event_type="lifecycle",
                fields={"provider_attempt_id": "attempt-1", "attempt": 1},
            ),
        ),
        usage=RemoteResourceUsage(wall_seconds=2.5, cpu_seconds=1.25),
        cleanup=RemoteCleanupOutcome(attempted=True, succeeded=True, resource_id="sandbox-1"),
        session_id="sandbox-1",
        provenance=remote_request_provenance(
            request,
            resolved=RemoteResolvedEnvironment(image="python:3.13", region="us-central-1", runtime="python-3.13"),
        ),
    )


def test_outbox_recovers_full_committed_result_and_ledger_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    request = _request()
    result = _result(request)
    first = ExternalEvalLedgerOutbox(path)

    claim = first.claim("primeintellect", request)
    assert claim.attempt_id == external_eval_attempt_id("primeintellect", request)
    assert claim.result is None
    assert first.commit("primeintellect", request, result) == claim.attempt_id
    first.mark_sink_delivered(claim.attempt_id)

    reopened = ExternalEvalLedgerOutbox(path)
    replay = reopened.claim("primeintellect", request)

    assert replay.result == result
    assert replay.sink_delivered is True
    assert reopened.ledger_entries() == (result.to_ledger_entry(),)
    assert reopened.committed_results() == (result,)
    assert reopened.statuses(unresolved_only=True) == ()
    assert path.stat().st_mode & 0o777 == 0o600


def test_outbox_refuses_to_redispatch_an_abandoned_claim(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    request = _request("ambiguous")
    first = ExternalEvalLedgerOutbox(path)
    claim = first.claim("primeintellect", request)

    with pytest.raises(ExternalEvalOutboxPendingError, match="reconcile provider accounting"):
        ExternalEvalLedgerOutbox(path).claim("primeintellect", request)

    unresolved = first.statuses(unresolved_only=True)
    assert len(unresolved) == 1
    assert unresolved[0].attempt_id == claim.attempt_id
    assert unresolved[0].state == "claimed"
    assert unresolved[0].timeout_seconds == 30.0
    assert unresolved[0].requested_cpu_cores == 1.0
    assert unresolved[0].requested_accelerator_count == 0


def test_outbox_commit_is_idempotent_and_conflicting_result_fails_closed(tmp_path: Path) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    request = _request()
    result = _result(request)
    outbox.claim("primeintellect", request)

    attempt_id = outbox.commit("primeintellect", request, result)
    assert outbox.commit("primeintellect", request, result) == attempt_id

    conflicting = replace(result, usage=RemoteResourceUsage(wall_seconds=9.0))
    with pytest.raises(ExternalEvalOutboxConflictError, match="different committed content"):
        outbox.commit("primeintellect", request, conflicting)


def test_outbox_detects_committed_result_corruption(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    outbox = ExternalEvalLedgerOutbox(path)
    request = _request()
    outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, _result(request))

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE remote_eval_outbox SET result_json = result_json || ' '")

    with pytest.raises(ValueError, match="result checksum mismatch"):
        outbox.claim("primeintellect", request)


class _Sandbox:
    id = "sandbox-1"
    image = "python:3.13"
    region = ""
    accelerator = None
    runtime = "python-3.13"


class _CommandResponse:
    stdout = '{"result":"ok"}'
    stderr = ""
    exit_code = 0


class _CountingAsyncClient:
    create_calls = 0

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def __aenter__(self) -> _CountingAsyncClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback

    async def create(self, request: Any) -> _Sandbox:
        del request
        type(self).create_calls += 1
        return _Sandbox()

    async def wait_for_creation(self, sandbox_id: str, max_attempts: int) -> None:
        del sandbox_id, max_attempts

    async def get(self, sandbox_id: str) -> _Sandbox:
        del sandbox_id
        return _Sandbox()

    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> _CommandResponse:
        del sandbox_id, command, timeout
        await asyncio.sleep(0)
        return _CommandResponse()

    async def delete(self, sandbox_id: str) -> dict[str, str]:
        return {"deleted": sandbox_id}


def test_sink_failure_replays_committed_ledger_without_second_provider_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _CountingAsyncClient,
    )
    _CountingAsyncClient.create_calls = 0
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    request = _request()
    failing = PrimeIntellectClient(
        api_key="test-key",
        ledger_outbox=outbox,
        ledger_sink=lambda _: (_ for _ in ()).throw(OSError("ledger unavailable")),
    )

    with pytest.raises(OSError, match="ledger unavailable"):
        failing.execute_request(request, max_retries=3, backoff_seconds=0)

    assert _CountingAsyncClient.create_calls == 1
    unresolved = outbox.statuses(unresolved_only=True)
    assert len(unresolved) == 1
    assert unresolved[0].state == "completed"
    assert "ledger unavailable" in unresolved[0].delivery_error

    delivered = []
    delivery_started = threading.Event()
    release_delivery = threading.Event()

    def blocking_sink(entry: object) -> None:
        delivered.append(entry)
        delivery_started.set()
        assert release_delivery.wait(timeout=2)

    recovered = PrimeIntellectClient(api_key="test-key", ledger_outbox=outbox, ledger_sink=blocking_sink)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(recovered.execute_request, request, max_retries=3, backoff_seconds=0)
        assert delivery_started.wait(timeout=2)
        assert recovered.cancel_request(request) is False
        release_delivery.set()
        replayed = future.result(timeout=2)

    assert replayed.succeeded is True
    assert _CountingAsyncClient.create_calls == 1
    assert delivered == [replayed.to_ledger_entry()]
    assert outbox.statuses(unresolved_only=True) == ()


def test_outbox_commit_failure_never_reenters_provider_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _CountingAsyncClient,
    )
    _CountingAsyncClient.create_calls = 0
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    request = _request("commit-failure")
    client = PrimeIntellectClient(api_key="test-key", ledger_outbox=outbox)
    monkeypatch.setattr(outbox, "commit", lambda *_: (_ for _ in ()).throw(OSError("disk unavailable")))

    with pytest.raises(OSError, match="disk unavailable"):
        client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert _CountingAsyncClient.create_calls == 1
    with pytest.raises(ExternalEvalOutboxPendingError, match="reconcile provider accounting"):
        PrimeIntellectClient(api_key="test-key", ledger_outbox=outbox).execute_request(
            request,
            max_retries=3,
            backoff_seconds=0,
        )
    assert _CountingAsyncClient.create_calls == 1


def test_runtime_factory_wires_the_durable_outbox_for_generation_and_campaigns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _CountingAsyncClient,
    )
    settings = AppSettings(
        executor_mode="primeintellect",
        primeintellect_api_key="test-key",
        runs_root=tmp_path / "runs",
    )

    runtime = build_execution_runtime(settings)

    assert runtime.remote_ledger_outbox is not None
    assert runtime.remote_ledger_outbox.path == tmp_path / "runs" / "external-evaluations" / "prime-ledger.sqlite3"
    assert runtime.remote_adapter is not None
    assert runtime.remote_adapter.ledger_outbox is runtime.remote_ledger_outbox
    assert runtime.unresolved_remote_evaluations() == ()

    result = runtime.remote_adapter.execute_request(_request("runtime-composition"), max_retries=0)

    assert runtime.remote_ledger_outbox.ledger_entries() == (result.to_ledger_entry(),)
    assert runtime.remote_ledger_outbox.committed_results() == (result,)
