from __future__ import annotations

from typing import Any

import pytest

from autocontext.execution.remote_execution import (
    RemoteAcceleratorRequest,
    RemoteExecutionRequest,
    RemoteInputArtifact,
    RemoteResourceRequest,
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
        _ = (sandbox_id, timeout)
        self.__class__.latest_command = command
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


def test_execute_strategy_raises_when_fallback_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autocontext.integrations.primeintellect.client.AsyncSandboxClient", _FailingAsyncClient)
    client = PrimeIntellectClient(api_key="test-key", allow_fallback=False)

    with pytest.raises(RuntimeError, match="boom"):
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


def test_accelerators_fail_clearly_unless_provider_advertises_support() -> None:
    request = RemoteExecutionRequest(
        task_id="gpu",
        image="research:latest",
        command="python train.py",
        resources=RemoteResourceRequest(accelerator=RemoteAcceleratorRequest("A100")),
    )

    with pytest.raises(UnsupportedRemoteCapabilityError, match="accelerator"):
        PrimeIntellectClient(api_key="test-key").execute_request(request)


def test_matched_trials_reuse_one_bounded_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
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

    results = PrimeIntellectClient(api_key="test-key").execute_requests(requests)

    assert len(_SuccessAsyncClient.created_requests) == before + 1
    assert {result.session_id for result in results} == {"sbx-1"}
    assert all(result.status == "success" for result in results)
