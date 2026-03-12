from __future__ import annotations

from typing import Any

import pytest

from autocontext.integrations.primeintellect.client import PrimeIntellectClient


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

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def __aenter__(self) -> _SuccessAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def list(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": [], **kwargs}

    async def create(self, request: Any) -> _FakeSandbox:
        _ = request
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
