from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from autocontext.execution.remote_execution import (
    RemoteAcceleratorRequest,
    RemoteExecutionRequest,
    RemoteInputArtifact,
    RemoteResourceRequest,
    RemoteSecretGrant,
)
from autocontext.integrations.primeintellect.client import (
    PrimeIntellectClient,
    UnsupportedRemoteCapabilityError,
)


class _FakeSandbox:
    def __init__(self, sandbox_id: str):
        self.id = sandbox_id


class _FakeCommandResponse:
    def __init__(self, stdout: str, stderr: str = "", exit_code: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _SuccessAsyncClient:
    latest_command: str = ""
    latest_timeout: int = 0
    deleted_ids: list[str] = []
    created_requests: list[Any] = []

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def __aenter__(self) -> _SuccessAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def list(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": [], **kwargs}

    async def create(self, request: Any) -> _FakeSandbox:
        self.__class__.created_requests.append(request)
        return _FakeSandbox("sbx-1")

    async def wait_for_creation(self, sandbox_id: str, max_attempts: int) -> None:
        _ = (sandbox_id, max_attempts)
        return None

    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> _FakeCommandResponse:
        _ = sandbox_id
        self.__class__.latest_command = command
        self.__class__.latest_timeout = timeout
        stdout = (
            '{"result":{"score":0.64,"winner":"challenger","summary":"ok","replay":[],"metrics":{},'
            '"validation_errors":[]},"replay":{"scenario":"grid_ctf","seed":123,"narrative":"ok","timeline":[]}}'
        )
        return _FakeCommandResponse(stdout=stdout)

    async def delete(self, sandbox_id: str) -> dict[str, Any]:
        self.__class__.deleted_ids.append(sandbox_id)
        return {"deleted": sandbox_id}


class _FailingAsyncClient(_SuccessAsyncClient):
    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> _FakeCommandResponse:
        _ = (sandbox_id, command, timeout)
        raise RuntimeError("boom")


class _ProvisionFailureAsyncClient(_SuccessAsyncClient):
    async def wait_for_creation(self, sandbox_id: str, max_attempts: int) -> None:
        _ = (sandbox_id, max_attempts)
        raise RuntimeError("provisioning unavailable")


class _AmbiguousCreateFailureAsyncClient(_SuccessAsyncClient):
    create_calls = 0

    async def create(self, request: Any) -> _FakeSandbox:
        _ = request
        self.__class__.create_calls += 1
        raise TimeoutError("response lost after create")


class _MissingSandboxIdAsyncClient(_SuccessAsyncClient):
    create_calls = 0

    async def create(self, request: Any) -> object:
        _ = request
        self.__class__.create_calls += 1
        return object()


class _TaskFailureAsyncClient(_SuccessAsyncClient):
    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> _FakeCommandResponse:
        _ = (sandbox_id, command, timeout)
        return _FakeCommandResponse(stdout="", stderr="bad candidate", exit_code=3)


class _TimeoutAsyncClient(_SuccessAsyncClient):
    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> _FakeCommandResponse:
        _ = (sandbox_id, command, timeout)
        raise TimeoutError("provider timeout")


class _CleanupFailureAsyncClient(_SuccessAsyncClient):
    async def delete(self, sandbox_id: str) -> dict[str, Any]:
        raise RuntimeError(f"could not delete {sandbox_id}")


class _ExitFailureAsyncClient(_SuccessAsyncClient):
    create_calls = 0

    async def create(self, request: Any) -> _FakeSandbox:
        self.__class__.create_calls += 1
        return await super().create(request)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        raise RuntimeError("SDK shutdown failed")


class _AmbiguousCreateAndExitFailureAsyncClient(_AmbiguousCreateFailureAsyncClient, _ExitFailureAsyncClient):
    pass


class _ProvisionAndExitFailureAsyncClient(_ProvisionFailureAsyncClient, _ExitFailureAsyncClient):
    pass


class _TimeoutAndExitFailureAsyncClient(_TimeoutAsyncClient, _ExitFailureAsyncClient):
    pass


class _TaskAndExitFailureAsyncClient(_TaskFailureAsyncClient, _ExitFailureAsyncClient):
    pass


class _ProviderAndExitFailureAsyncClient(_FailingAsyncClient, _ExitFailureAsyncClient):
    pass


class _HangingCleanupAsyncClient(_SuccessAsyncClient):
    async def delete(self, sandbox_id: str) -> dict[str, Any]:
        _ = sandbox_id
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _ProviderAndCleanupFailureAsyncClient(_SuccessAsyncClient):
    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> _FakeCommandResponse:
        _ = (sandbox_id, command, timeout)
        raise RuntimeError("provider failed after sandbox creation")

    async def delete(self, sandbox_id: str) -> dict[str, Any]:
        raise RuntimeError(f"could not delete {sandbox_id}")


class _CancelableAsyncClient(_SuccessAsyncClient):
    command_started = threading.Event()
    sandbox_deleted = threading.Event()
    deleted_ids: list[str] = []
    created_requests: list[Any] = []

    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> _FakeCommandResponse:
        _ = (sandbox_id, command, timeout)
        self.__class__.command_started.set()
        while not self.__class__.sandbox_deleted.is_set():
            await asyncio.sleep(0.01)
        raise RuntimeError("sandbox deleted")

    async def delete(self, sandbox_id: str) -> dict[str, Any]:
        self.__class__.deleted_ids.append(sandbox_id)
        self.__class__.sandbox_deleted.set()
        return {"deleted": sandbox_id}


def test_execute_strategy_uses_sandbox_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _SuccessAsyncClient)
    client = PrimeIntellectClient(api_key="test-key")

    result = client.execute_strategy(
        scenario_name="grid_ctf",
        strategy={"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
        seed=123,
        timeout_seconds=10.0,
        max_memory_mb=512,
        network_access=False,
    )

    assert result["result"]["winner"] == "challenger"
    assert "python - <<'PY'" in _SuccessAsyncClient.latest_command
    assert _SuccessAsyncClient.deleted_ids[-1] == "sbx-1"


def test_fractional_remote_timeout_is_never_rounded_below_the_declared_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _SuccessAsyncClient)
    request = RemoteExecutionRequest(
        task_id="fractional-timeout",
        image="python:3.13",
        command="true",
        timeout_seconds=1.1,
    )

    result = PrimeIntellectClient(api_key="test-key").execute_request(request, max_retries=0)

    assert result.succeeded
    assert _SuccessAsyncClient.latest_timeout == 2


def test_execute_strategy_honors_configured_memory_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _SuccessAsyncClient)
    client = PrimeIntellectClient(api_key="test-key", memory_gb=0.25)

    client.execute_strategy(
        scenario_name="grid_ctf",
        strategy={"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
        seed=123,
        timeout_seconds=10.0,
        max_memory_mb=512,
        network_access=False,
    )

    assert _SuccessAsyncClient.created_requests[-1].memory_gb == 0.25


def test_execute_strategy_falls_back_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _FailingAsyncClient)
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=True)

    result = client.execute_strategy(
        scenario_name="grid_ctf",
        strategy={"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
        seed=123,
        timeout_seconds=10.0,
        max_memory_mb=512,
        network_access=False,
        max_retries=0,
    )

    assert result["result"]["summary"] == "primeintellect execution unavailable"


def test_prime_fallback_is_fail_closed_by_default() -> None:
    assert PrimeIntellectClient(api_key="test-key").allow_fallback is False


def test_prime_client_rejects_invalid_limits_and_redacts_api_key_repr() -> None:
    client = PrimeIntellectClient(api_key="super-secret-prime-key")

    assert "super-secret-prime-key" not in repr(client)
    with pytest.raises(ValueError, match="API key"):
        PrimeIntellectClient(api_key="   ")
    with pytest.raises(ValueError, match="positive and finite"):
        PrimeIntellectClient(api_key="test-key", cpu_cores=float("nan"))
    with pytest.raises(ValueError, match="non-negative and finite"):
        client.execute_request(
            RemoteExecutionRequest(task_id="invalid-retry", image="python:3.13", command="true"),
            backoff_seconds=float("nan"),
        )


def test_execute_strategy_raises_when_fallback_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _FailingAsyncClient)
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=False)
    before = len(_FailingAsyncClient.created_requests)

    with pytest.raises(RuntimeError, match="boom"):
        client.execute_strategy(
            scenario_name="grid_ctf",
            strategy={"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            seed=123,
            timeout_seconds=10.0,
            max_memory_mb=512,
            network_access=False,
            max_retries=2,
            backoff_seconds=0,
        )

    assert len(_FailingAsyncClient.created_requests) == before + 1


def test_pre_command_provisioning_failures_remain_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _ProvisionFailureAsyncClient,
    )
    ledger = []
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=False, ledger_sink=ledger.append)
    before = len(_ProvisionFailureAsyncClient.created_requests)
    request = RemoteExecutionRequest(task_id="provision-retry", image="python:3.13", command="true")

    result = client.execute_request(request, max_retries=2, backoff_seconds=0)

    assert result.status == "provider_error"
    assert result.cleanup.succeeded is True
    assert result.cleanup.resource_id == "sbx-1"
    assert len(_ProvisionFailureAsyncClient.created_requests) == before + 3
    assert result.usage.wall_seconds > 0
    assert ledger == [result.to_ledger_entry()]


def test_sdk_context_exit_failure_after_command_is_terminal_and_preserves_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _ExitFailureAsyncClient,
    )
    _ExitFailureAsyncClient.create_calls = 0
    ledger = []
    client = PrimeIntellectClient(api_key="test-key", ledger_sink=ledger.append)
    request = RemoteExecutionRequest(task_id="context-exit", image="python:3.13", command="true")

    result = client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert _ExitFailureAsyncClient.create_calls == 1
    assert result.status == "success"
    assert result.cleanup.succeeded is True
    assert result.session_id == "sbx-1"
    assert result.stdout
    assert "SDK shutdown failed" in result.error
    assert [event.event_type for event in result.events][-1] == "provider_client_exit_error"
    assert ledger == [result.to_ledger_entry()]
    assert ledger[0].candidate_succeeded is True
    assert ledger[0].infrastructure_succeeded is False


@pytest.mark.parametrize(
    ("sdk_client", "expected_status", "primary_detail"),
    [
        (_ProvisionAndExitFailureAsyncClient, "provider_error", "provisioning unavailable"),
        (_TimeoutAndExitFailureAsyncClient, "timeout", "provider timeout"),
        (_TaskAndExitFailureAsyncClient, "task_error", "bad candidate"),
        (_ProviderAndExitFailureAsyncClient, "provider_error", "outcome is unknown"),
    ],
)
def test_sdk_exit_failure_preserves_primary_outcome_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    sdk_client: type[_SuccessAsyncClient],
    expected_status: str,
    primary_detail: str,
) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", sdk_client)
    sdk_client.create_calls = 0
    ledger = []
    client = PrimeIntellectClient(api_key="test-key", ledger_sink=ledger.append)
    request = RemoteExecutionRequest(task_id=f"exit-{expected_status}", image="python:3.13", command="true")

    result = client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert sdk_client.create_calls == 1
    assert result.status == expected_status
    assert primary_detail in result.error
    assert "SDK shutdown failed" in result.error
    assert [event.event_type for event in result.events][-1] == "provider_client_exit_error"
    assert ledger == [result.to_ledger_entry()]
    assert ledger[0].infrastructure_succeeded is False


def test_ambiguous_create_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AmbiguousCreateFailureAsyncClient,
    )
    _AmbiguousCreateFailureAsyncClient.create_calls = 0
    ledger = []
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=True, ledger_sink=ledger.append)
    request = RemoteExecutionRequest(task_id="ambiguous-create", image="python:3.13", command="true")

    result = client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert result.status == "cleanup_error"
    assert result.cleanup.succeeded is False
    assert "no provider resource id" in result.error
    assert _AmbiguousCreateFailureAsyncClient.create_calls == 1
    assert ledger == [result.to_ledger_entry()]


def test_ambiguous_create_plus_context_exit_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AmbiguousCreateAndExitFailureAsyncClient,
    )
    _AmbiguousCreateAndExitFailureAsyncClient.create_calls = 0
    ledger = []
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=True, ledger_sink=ledger.append)
    request = RemoteExecutionRequest(task_id="ambiguous-create-exit", image="python:3.13", command="true")

    result = client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert result.status == "cleanup_error"
    assert result.cleanup.succeeded is False
    assert "response lost after create" in result.error
    assert "SDK shutdown failed" in result.error
    assert _AmbiguousCreateAndExitFailureAsyncClient.create_calls == 1
    assert [event.event_type for event in result.events] == ["provider_error", "provider_client_exit_error"]
    assert ledger == [result.to_ledger_entry()]
    assert ledger[0].infrastructure_succeeded is False


def test_create_response_without_resource_id_is_treated_as_an_ambiguous_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _MissingSandboxIdAsyncClient,
    )
    _MissingSandboxIdAsyncClient.create_calls = 0
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=True)
    request = RemoteExecutionRequest(task_id="missing-sandbox-id", image="python:3.13", command="true")

    result = client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert result.status == "cleanup_error"
    assert "without a resource id" in result.error
    assert _MissingSandboxIdAsyncClient.create_calls == 1


def test_execute_strategy_does_not_hide_typed_task_failure_when_fallback_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _TaskFailureAsyncClient)
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=False)

    with pytest.raises(RuntimeError, match="primeintellect task_error: bad candidate"):
        client.execute_strategy(
            scenario_name="grid_ctf",
            strategy={"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            seed=123,
            timeout_seconds=10.0,
            max_memory_mb=512,
            network_access=False,
        )


def test_build_eval_command_does_not_reference_undefined_logging() -> None:
    client = PrimeIntellectClient(api_key="test-key")

    command = client._build_eval_command(
        scenario_name="grid_ctf",
        strategy={"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
        seed=123,
    )

    assert "logging.getLogger" not in command


def test_prime_client_source_has_no_embedded_game_scoring_logic() -> None:
    from pathlib import Path

    source = Path(__file__).parents[1] / "src" / "autocontext" / "integrations" / "primeintellect" / "client.py"
    text = source.read_text()

    assert "capture_progress" not in text
    assert "mobility_weight" not in text
    assert "scenario_remote_task" in text


def test_generic_research_request_transports_inputs_resources_and_typed_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _SuccessAsyncClient)
    ledger = []
    client = PrimeIntellectClient(api_key="test-key", ledger_sink=ledger.append)
    request = RemoteExecutionRequest(
        task_id="research-task",
        image="python:3.13",
        command="python analyze.py",
        resources=RemoteResourceRequest(cpu_cores=2, memory_gb=4, disk_gb=8),
        input_artifacts=(RemoteInputArtifact("analyze.py", b"print('ok')"),),
    )

    result = client.execute_request(request)

    assert result.status == "success"
    assert result.cleanup.succeeded is True
    assert "analyze.py" in _SuccessAsyncClient.latest_command
    created = _SuccessAsyncClient.created_requests[-1]
    assert created.cpu_cores == 2
    assert created.memory_gb == 4
    assert ledger[-1].status == "success"


@pytest.mark.parametrize(
    ("fake_client", "expected_status"),
    [
        (_TaskFailureAsyncClient, "task_error"),
        (_TimeoutAsyncClient, "timeout"),
        (_CleanupFailureAsyncClient, "cleanup_error"),
    ],
)
def test_generic_remote_failures_remain_distinguishable(
    monkeypatch: pytest.MonkeyPatch,
    fake_client: type[_SuccessAsyncClient],
    expected_status: str,
) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", fake_client)
    request = RemoteExecutionRequest(task_id="failure", image="python:3.13", command="false")

    result = PrimeIntellectClient(api_key="test-key").execute_request(request, max_retries=0)

    assert result.status == expected_status


def test_provider_failure_with_cleanup_failure_is_terminal_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _ProviderAndCleanupFailureAsyncClient,
    )
    before = len(_ProviderAndCleanupFailureAsyncClient.created_requests)
    ledger = []
    client = PrimeIntellectClient(api_key="test-key", ledger_sink=ledger.append)
    request = RemoteExecutionRequest(task_id="leaked-attempt", image="python:3.13", command="false")

    result = client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert result.status == "cleanup_error"
    assert result.cleanup.succeeded is False
    assert result.cleanup.resource_id == "sbx-1"
    assert "provider failed after sandbox creation" in result.error
    assert "could not delete" in result.error
    assert len(_ProviderAndCleanupFailureAsyncClient.created_requests) == before + 1
    assert ledger == [result.to_ledger_entry()]


def test_cleanup_call_is_bounded_and_reported_as_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _HangingCleanupAsyncClient,
    )
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client._CLEANUP_TIMEOUT_SECONDS",
        0.01,
    )
    request = RemoteExecutionRequest(task_id="cleanup-timeout", image="python:3.13", command="true")

    result = PrimeIntellectClient(api_key="test-key").execute_request(request, max_retries=3, backoff_seconds=0)

    assert result.status == "cleanup_error"
    assert result.cleanup.succeeded is False
    assert result.cleanup.resource_id == "sbx-1"
    assert "cleanup timed out" in result.error


def test_ledger_failure_does_not_retry_completed_remote_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _SuccessAsyncClient)
    before = len(_SuccessAsyncClient.created_requests)
    client = PrimeIntellectClient(
        api_key="test-key",
        ledger_sink=lambda _: (_ for _ in ()).throw(OSError("ledger unavailable")),
    )
    request = RemoteExecutionRequest(task_id="ledger-failure", image="python:3.13", command="true")

    with pytest.raises(OSError, match="ledger unavailable"):
        client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert len(_SuccessAsyncClient.created_requests) == before + 1


def test_accelerators_fail_clearly_unless_provider_advertises_support() -> None:
    request = RemoteExecutionRequest(
        task_id="gpu",
        image="research:latest",
        command="python train.py",
        resources=RemoteResourceRequest(accelerator=RemoteAcceleratorRequest("A100")),
    )

    with pytest.raises(UnsupportedRemoteCapabilityError, match="accelerator"):
        PrimeIntellectClient(api_key="test-key").execute_request(request)


def test_secret_grants_are_revalidated_at_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    expires_at = time.time() + 60
    request = RemoteExecutionRequest(
        task_id="secret-task",
        image="research:latest",
        command="python task.py",
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", expires_at),),
    )
    client = PrimeIntellectClient(
        api_key="test-key",
        provider_capabilities={"secret_grants": True},
    )
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.time.time", lambda: expires_at + 1)

    with pytest.raises(ValueError, match="expired before dispatch: dataset"):
        client.execute_request(request)


def test_secret_grants_are_revalidated_before_every_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    expires_at = time.time() + 60
    request = RemoteExecutionRequest(
        task_id="secret-retry",
        image="research:latest",
        command="python task.py",
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", expires_at),),
    )
    client = PrimeIntellectClient(
        api_key="test-key",
        allow_fallback=True,
        provider_capabilities={"secret_grants": True},
    )
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _ProvisionFailureAsyncClient,
    )
    clock_values = iter((expires_at - 1, expires_at - 1, expires_at + 1))
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.time.time", lambda: next(clock_values))
    before = len(_ProvisionFailureAsyncClient.created_requests)

    with pytest.raises(ValueError, match="expired before dispatch: dataset"):
        client.execute_request(request, max_retries=1, backoff_seconds=0)

    assert len(_ProvisionFailureAsyncClient.created_requests) == before + 1


def test_matched_trials_reuse_stays_disabled_when_capability_is_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _SuccessAsyncClient)
    before = len(_SuccessAsyncClient.created_requests)
    requests = tuple(
        RemoteExecutionRequest(
            task_id=f"trial-{index}",
            image="python:3.13",
            command="python task.py",
            lifecycle="reuse_matched_trials",
            max_reuse_tasks=2,
        )
        for index in range(2)
    )

    with pytest.raises(UnsupportedRemoteCapabilityError, match="session_reuse"):
        PrimeIntellectClient(api_key="test-key").execute_requests(requests)

    forced_client = PrimeIntellectClient(
        api_key="test-key",
        provider_capabilities={"session_reuse": True},
    )
    assert forced_client.capabilities()["session_reuse"] is False
    with pytest.raises(UnsupportedRemoteCapabilityError, match="verified reset primitive"):
        forced_client.execute_requests(requests)

    assert len(_SuccessAsyncClient.created_requests) == before


def test_cancel_request_deletes_active_sandbox_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _CancelableAsyncClient)
    _CancelableAsyncClient.command_started.clear()
    _CancelableAsyncClient.sandbox_deleted.clear()
    _CancelableAsyncClient.deleted_ids.clear()
    _CancelableAsyncClient.created_requests.clear()
    client = PrimeIntellectClient(api_key="test-key")
    request = RemoteExecutionRequest(task_id="cancel-me", image="python:3.13", command="python task.py")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.execute_request, request, max_retries=2, backoff_seconds=0)
        assert _CancelableAsyncClient.command_started.wait(timeout=2)
        assert client.cancel_request("cancel-me") is True
        result = future.result(timeout=2)

    assert result.status == "provider_error"
    assert result.error == "remote task canceled"
    assert result.cleanup.succeeded is True
    assert _CancelableAsyncClient.deleted_ids == ["sbx-1"]
    assert len(_CancelableAsyncClient.created_requests) == 1
    assert client.cancel_request(request) is False


def test_cancel_after_result_commit_cannot_acknowledge_a_successful_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _SuccessAsyncClient)
    ledger_started = threading.Event()
    release_ledger = threading.Event()

    def blocking_ledger(_: object) -> None:
        ledger_started.set()
        assert release_ledger.wait(timeout=2)

    client = PrimeIntellectClient(api_key="test-key", ledger_sink=blocking_ledger)
    request = RemoteExecutionRequest(task_id="commit-race", image="python:3.13", command="true")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.execute_request, request, max_retries=0)
        assert ledger_started.wait(timeout=2)
        assert client.cancel_request(request) is False
        release_ledger.set()
        result = future.result(timeout=2)

    assert result.status == "success"
    assert not any(event.event_type == "canceled" for event in result.events)
