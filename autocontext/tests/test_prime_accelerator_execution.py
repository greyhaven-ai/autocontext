from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from prime_sandboxes import CommandResponse, CreateSandboxRequest

from autocontext.config.settings import AppSettings
from autocontext.context_bundles.runtime_evaluator import materialize_runtime_fixture
from autocontext.execution.campaign_runtime import (
    CampaignPlan,
    _campaign_remote_requirements,
    _job_request,
    derive_campaign_evaluation_identity,
    run_campaign_plan,
)
from autocontext.execution.remote_execution import (
    RemoteAcceleratorRequest,
    RemoteExecutionRequest,
    RemoteProviderCapabilities,
    RemoteResourceRequest,
    remote_request_sha256,
)
from autocontext.execution.runtime_factory import prime_default_requirements, prime_resource_capabilities
from autocontext.integrations.primeintellect.accelerators import create_kwargs
from autocontext.integrations.primeintellect.client import (
    PrimeIntellectClient,
    UnsupportedRemoteCapabilityError,
)
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE
from autocontext.scenarios.othello import OthelloScenario
from autocontext.server.app import _build_environments_msg
from autocontext.server.run_manager import RunManager


class _AcceleratorSandbox:
    def __init__(
        self,
        *,
        image: str = PINNED_PYTHON_RUNTIME_IMAGE,
        region: str = "us-central-1",
        kind: str = "H100",
        count: int = 2,
    ) -> None:
        self.id = "sbx-gpu-1"
        self.docker_image = image
        self.region = region
        self.gpu_type = kind
        self.gpu_count = count


class _AcceleratorAsyncClient:
    created_requests: list[Any] = []
    command_calls = 0
    get_calls = 0
    deleted_ids: list[str] = []
    initial_sandbox = _AcceleratorSandbox()
    sandbox = _AcceleratorSandbox()

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def __aenter__(self) -> _AcceleratorAsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def create(self, request: Any) -> _AcceleratorSandbox:
        self.__class__.created_requests.append(request)
        return self.__class__.initial_sandbox

    async def wait_for_creation(self, sandbox_id: str, max_attempts: int) -> None:
        _ = (sandbox_id, max_attempts)

    async def get(self, sandbox_id: str) -> _AcceleratorSandbox:
        _ = sandbox_id
        self.__class__.get_calls += 1
        return self.__class__.sandbox

    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> CommandResponse:
        _ = (sandbox_id, command, timeout)
        self.__class__.command_calls += 1
        return CommandResponse(stdout="{}", stderr="", exit_code=0)

    async def delete(self, sandbox_id: str) -> dict[str, str]:
        self.__class__.deleted_ids.append(sandbox_id)
        return {"deleted": sandbox_id}


class _FailingCommandAcceleratorClient(_AcceleratorAsyncClient):
    async def execute_command(self, sandbox_id: str, command: str, timeout: int) -> CommandResponse:
        _ = (sandbox_id, command, timeout)
        self.__class__.command_calls += 1
        raise RuntimeError("accelerator command unavailable")


def _capabilities(*, kinds: dict[str, int] | None = None) -> RemoteProviderCapabilities:
    return RemoteProviderCapabilities(
        images=frozenset({PINNED_PYTHON_RUNTIME_IMAGE}),
        regions=frozenset({"us-central-1"}),
        accelerator_limits=kinds or {"H100": 2},
        telemetry=frozenset({"hardware_identity"}),
    )


def _request(*, kind: str = "H100", count: int = 2, region: str = "us-central-1") -> RemoteExecutionRequest:
    return RemoteExecutionRequest(
        task_id="gpu-task",
        image=PINNED_PYTHON_RUNTIME_IMAGE,
        command="python benchmark.py",
        resources=RemoteResourceRequest(
            cpu_cores=4,
            memory_gb=16,
            disk_gb=40,
            accelerator=RemoteAcceleratorRequest(kind, count=count),
        ),
        region=region,
        required_telemetry=frozenset({"hardware_identity"}),
    )


def _reset_fake_client(client: type[_AcceleratorAsyncClient] = _AcceleratorAsyncClient) -> None:
    client.created_requests.clear()
    client.command_calls = 0
    client.get_calls = 0
    client.deleted_ids.clear()
    client.initial_sandbox = _AcceleratorSandbox()
    client.sandbox = _AcceleratorSandbox()


def test_accelerator_request_reaches_prime_unchanged_and_records_resolved_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_fake_client()
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AcceleratorAsyncClient,
    )
    ledger = []
    client = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        resource_capabilities=_capabilities(),
        ledger_sink=ledger.append,
    )
    request = _request()

    result = client.execute_request(request, max_retries=0)

    assert result.status == "success"
    created = _AcceleratorAsyncClient.created_requests[-1]
    assert created.docker_image == request.image
    assert created.cpu_cores == 4
    assert created.memory_gb == 16
    assert created.disk_size_gb == 40
    assert created.gpu_type == "H100"
    assert created.gpu_count == 2
    assert created.vm is True
    assert created.region == "us-central-1"
    assert created.idempotency_key == remote_request_sha256(request)
    assert result.provenance.request_sha256 == remote_request_sha256(request)
    assert result.provenance.requested_accelerator_kind == "H100"
    assert result.provenance.requested_accelerator_count == 2
    assert result.provenance.resolved.image == request.image
    assert result.provenance.resolved.region == "us-central-1"
    assert result.provenance.resolved.accelerator_kind == "H100"
    assert result.provenance.resolved.accelerator_count == 2
    assert result.provenance.resolved.runtime.startswith("prime-sandboxes/")
    assert result.usage.accelerator_seconds is None
    assert result.usage.accelerator_peak_memory_mb is None
    assert ledger == [result.to_ledger_entry()]
    assert ledger[0].provenance == result.provenance
    assert ledger[0].usage == result.usage


def test_accelerator_kwargs_satisfy_the_real_prime_sdk_request_contract() -> None:
    request = _request()

    sdk_request = CreateSandboxRequest(
        **create_kwargs(
            request,
            timeout_minutes=30,
            network_access=True,
        )
    )

    assert sdk_request.gpu_type == "H100"
    assert sdk_request.gpu_count == 2
    assert sdk_request.vm is True
    assert sdk_request.region == "us-central-1"
    assert sdk_request.idempotency_key == remote_request_sha256(request)
    assert {"gpu_type", "gpu_count", "vm", "region", "idempotency_key"} <= set(CreateSandboxRequest.model_fields)
    assert set(CommandResponse.model_fields) == {"stdout", "stderr", "exit_code"}


def test_incompatible_prime_sdk_model_fails_before_provider_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    class _LegacyCreateSandboxRequest:
        model_fields = {"gpu_count": object()}

    _reset_fake_client()
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AcceleratorAsyncClient,
    )
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.CreateSandboxRequest",
        _LegacyCreateSandboxRequest,
    )
    client = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        resource_capabilities=_capabilities(),
    )

    with pytest.raises(UnsupportedRemoteCapabilityError, match="missing accelerator placement fields"):
        client.execute_request(_request(), max_retries=3)

    assert _AcceleratorAsyncClient.created_requests == []


@pytest.mark.parametrize(
    ("remote_request", "capabilities", "message"),
    [
        (_request(kind="A100"), _capabilities(), "accelerator kind"),
        (_request(count=2), _capabilities(kinds={"H100": 1}), "exceeds"),
        (_request(region="eu-west-1"), _capabilities(), "region"),
        (
            replace(_request(), image=f"example.invalid/gpu@sha256:{'a' * 64}"),
            _capabilities(),
            "image",
        ),
    ],
)
def test_capability_mismatch_fails_before_provider_creation(
    monkeypatch: pytest.MonkeyPatch,
    remote_request: RemoteExecutionRequest,
    capabilities: RemoteProviderCapabilities,
    message: str,
) -> None:
    _reset_fake_client()
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AcceleratorAsyncClient,
    )
    client = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        resource_capabilities=capabilities,
    )

    with pytest.raises(UnsupportedRemoteCapabilityError, match=message):
        client.execute_request(remote_request, max_retries=3)

    assert _AcceleratorAsyncClient.created_requests == []
    assert _AcceleratorAsyncClient.command_calls == 0


@pytest.mark.parametrize(
    ("sandbox", "message"),
    [
        (_AcceleratorSandbox(kind="A100"), "resolved accelerator"),
        (_AcceleratorSandbox(count=1), "accelerator count"),
        (_AcceleratorSandbox(region="eu-west-1"), "resolved region"),
        (_AcceleratorSandbox(image=f"example.invalid/drift@sha256:{'b' * 64}"), "resolved image"),
    ],
)
def test_provider_drift_is_terminal_before_command_and_never_retried(
    monkeypatch: pytest.MonkeyPatch,
    sandbox: _AcceleratorSandbox,
    message: str,
) -> None:
    _reset_fake_client()
    # The create acknowledgement matches the request; only the final sandbox
    # fetched after provisioning exposes the drift.
    _AcceleratorAsyncClient.initial_sandbox = _AcceleratorSandbox()
    _AcceleratorAsyncClient.sandbox = sandbox
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AcceleratorAsyncClient,
    )
    client = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        resource_capabilities=_capabilities(),
    )

    result = client.execute_request(_request(), max_retries=3, backoff_seconds=0)

    assert result.status == "provider_error"
    assert message in result.error
    assert len(_AcceleratorAsyncClient.created_requests) == 1
    assert _AcceleratorAsyncClient.get_calls == 1
    assert _AcceleratorAsyncClient.command_calls == 0
    assert _AcceleratorAsyncClient.deleted_ids == ["sbx-gpu-1"]
    assert result.retryable is False


def test_missing_cpu_hardware_identity_is_terminal_before_command(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_fake_client()
    _AcceleratorAsyncClient.initial_sandbox = _AcceleratorSandbox(region="", kind="", count=0)
    _AcceleratorAsyncClient.sandbox = _AcceleratorSandbox(image="", region="", kind="", count=0)
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AcceleratorAsyncClient,
    )
    client = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        resource_capabilities=_capabilities(),
    )
    request = RemoteExecutionRequest(
        task_id="cpu-hardware-identity",
        image=PINNED_PYTHON_RUNTIME_IMAGE,
        command="python benchmark.py",
        required_telemetry=frozenset({"hardware_identity"}),
    )

    result = client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert result.status == "provider_error"
    assert "provider omitted required telemetry: hardware_identity" in result.error
    assert len(_AcceleratorAsyncClient.created_requests) == 1
    assert _AcceleratorAsyncClient.get_calls == 1
    assert _AcceleratorAsyncClient.command_calls == 0
    assert _AcceleratorAsyncClient.deleted_ids == ["sbx-gpu-1"]


def test_unsupported_sdk_telemetry_fails_before_provider_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_fake_client()
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AcceleratorAsyncClient,
    )
    advertised = replace(
        _capabilities(),
        telemetry=frozenset({"hardware_identity", "accelerator_usage"}),
    )
    client = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        resource_capabilities=advertised,
    )
    request = replace(
        _request(),
        required_telemetry=frozenset({"hardware_identity", "accelerator_usage"}),
    )

    with pytest.raises(UnsupportedRemoteCapabilityError, match="SDK does not expose.*accelerator_usage"):
        client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert _AcceleratorAsyncClient.created_requests == []
    assert _AcceleratorAsyncClient.command_calls == 0


def test_accelerator_memory_selection_fails_before_provider_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_fake_client()
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AcceleratorAsyncClient,
    )
    request = replace(
        _request(),
        resources=replace(
            _request().resources,
            accelerator=RemoteAcceleratorRequest("H100", count=2, memory_gb=80),
        ),
    )
    client = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        resource_capabilities=replace(_capabilities(), accelerator_memory_selection=True),
    )

    with pytest.raises(UnsupportedRemoteCapabilityError, match="selecting accelerator memory"):
        client.execute_request(request, max_retries=0)

    assert _AcceleratorAsyncClient.created_requests == []
    assert _AcceleratorAsyncClient.command_calls == 0


def test_accelerator_failure_cannot_use_enabled_cpu_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_fake_client(_FailingCommandAcceleratorClient)
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _FailingCommandAcceleratorClient,
    )
    request = _request(count=2)
    client = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        allow_fallback=True,
        default_requirements=request.requirements,
        resource_capabilities=_capabilities(),
    )

    with pytest.raises(RuntimeError, match="primeintellect provider_error"):
        client.execute_strategy(
            scenario_name="grid_ctf",
            strategy={"aggression": 0.6, "defense": 0.4, "path_bias": 0.5},
            seed=123,
            timeout_seconds=10,
            max_memory_mb=16_384,
            network_access=False,
            max_retries=0,
        )

    assert len(_FailingCommandAcceleratorClient.created_requests) == 1


def _settings(**updates: Any) -> AppSettings:
    values: dict[str, Any] = {
        "executor_mode": "primeintellect",
        "primeintellect_api_key": "test-key",
        "primeintellect_docker_image": PINNED_PYTHON_RUNTIME_IMAGE,
        "primeintellect_supported_accelerator_kinds": "H100",
        "primeintellect_max_accelerator_count": 2,
        "primeintellect_supported_regions": "us-central-1",
        "primeintellect_supported_images": PINNED_PYTHON_RUNTIME_IMAGE,
        "primeintellect_available_telemetry": "hardware_identity",
    }
    values.update(updates)
    return AppSettings(**values)


def test_settings_build_explicit_default_request_and_provider_capabilities() -> None:
    settings = _settings(
        primeintellect_accelerator_kind="H100",
        primeintellect_accelerator_count=2,
        primeintellect_region="us-central-1",
        primeintellect_required_telemetry="hardware_identity",
    )

    requirements = prime_default_requirements(settings)
    capabilities = prime_resource_capabilities(settings)

    assert requirements.resources.accelerator == RemoteAcceleratorRequest("H100", count=2)
    assert requirements.region == "us-central-1"
    assert requirements.required_telemetry == frozenset({"hardware_identity"})
    assert capabilities.mismatch_reason(requirements) == ""


def test_server_environment_protocol_advertises_accelerator_requirements() -> None:
    settings = _settings(
        primeintellect_accelerator_kind="H100",
        primeintellect_accelerator_count=2,
        primeintellect_region="us-central-1",
        primeintellect_required_telemetry="hardware_identity",
    )
    manager = RunManager(MagicMock(), MagicMock(), settings)

    message = _build_environments_msg(manager.get_environment_info())

    prime = next(executor for executor in message.executors if executor.mode == "primeintellect")
    assert prime.resources is not None
    assert prime.resources.accelerator is not None
    assert prime.resources.accelerator.kind == "H100"
    assert prime.resources.accelerator.count == 2
    assert prime.resources.region == "us-central-1"
    assert prime.resources.required_telemetry == ["hardware_identity"]


@pytest.mark.parametrize(
    "updates",
    [
        {"primeintellect_accelerator_kind": "H100"},
        {"primeintellect_accelerator_count": 1},
        {
            "primeintellect_accelerator_kind": "A100",
            "primeintellect_accelerator_count": 1,
        },
        {
            "primeintellect_accelerator_kind": "H100",
            "primeintellect_accelerator_count": 3,
        },
        {"primeintellect_region": "eu-west-1"},
        {
            "primeintellect_accelerator_kind": "H100",
            "primeintellect_accelerator_count": 1,
            "primeintellect_required_telemetry": "accelerator_usage",
        },
        {"primeintellect_available_telemetry": "hardware_identity,accelerator_usage"},
    ],
)
def test_settings_reject_incomplete_or_unsupported_accelerator_configuration(updates: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _settings(**updates)


def _campaign_plan(settings: AppSettings, *, kind: str) -> CampaignPlan:
    identity = derive_campaign_evaluation_identity(settings, "othello")
    fixture = materialize_runtime_fixture(OthelloScenario(), 11).digest
    return CampaignPlan.from_dict(
        {
            "schema_version": 1,
            "campaign_id": "gpu-campaign",
            "run_id": "gpu-run",
            "scenario_name": "othello",
            "budget": {"jobs": 1, "wall_seconds": 60, "compute_units": 60},
            "jobs": [
                {
                    "job_id": "gpu-trial",
                    "idempotency_key": "gpu-trial-v1",
                    "branch_id": "gpu-branch",
                    "objective": "validate accelerator placement",
                    "strategy": {
                        "mobility_weight": 1.0,
                        "corner_weight": 0.5,
                        "stability_weight": 0.5,
                    },
                    "seed": 11,
                    "lane_id": "confirmation",
                    "fixture_digest": fixture,
                    "evaluator_epoch": identity.evaluator_epoch,
                    "verifier_contract_ref": identity.verifier_contract_ref,
                    "timeout_seconds": 30,
                    "max_memory_mb": 2048,
                    "remote": {
                        "image": PINNED_PYTHON_RUNTIME_IMAGE,
                        "accelerator": {"kind": kind, "count": 1},
                        "region": "us-central-1",
                        "required_telemetry": ["hardware_identity"],
                    },
                    "reservation": {"jobs": 1},
                }
            ],
        }
    )


def test_campaign_request_fingerprint_binds_accelerator_and_conservative_compute_budget() -> None:
    settings = _settings()
    plan = _campaign_plan(settings, kind="H100")
    item = plan.jobs[0]
    requirements = _campaign_remote_requirements(settings, item)

    request = _job_request(plan, item, remote_requirements=requirements)

    assert request.resources.accelerator_kind == "H100"
    assert request.resources.accelerator_count == 1
    assert "accelerator" in request.required_capabilities
    assert any(value.startswith("remote_requirements:") for value in request.required_capabilities)
    assert request.reservation.compute_units == pytest.approx(30.0)
    assert request.lane.execution_environment_digest
    payload = request.payload["remote_requirements"]
    assert isinstance(payload, dict)
    assert payload["resources"]["accelerator"] == {"kind": "H100", "count": 1}
    assert payload["region"] == "us-central-1"


def test_campaign_capability_mismatch_fails_before_state_or_paid_dispatch(tmp_path: Path) -> None:
    settings = _settings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        event_stream_path=tmp_path / "events.ndjson",
    )
    state_root = tmp_path / "campaign-state"

    with pytest.raises(UnsupportedRemoteCapabilityError, match="accelerator kind"):
        run_campaign_plan(_campaign_plan(settings, kind="A100"), settings, state_root=state_root)

    assert not state_root.exists()


def test_accelerator_requirements_change_campaign_lane_identity() -> None:
    settings = _settings()
    h100 = _campaign_plan(settings, kind="H100")
    h100_requirements = _campaign_remote_requirements(settings, h100.jobs[0])
    h100_request = _job_request(h100, h100.jobs[0], remote_requirements=h100_requirements)
    remote = h100.jobs[0].remote
    assert remote is not None
    accelerator = remote.accelerator
    assert accelerator is not None
    a100_item = h100.jobs[0].model_copy(
        update={"remote": remote.model_copy(update={"accelerator": accelerator.model_copy(update={"kind": "A100"})})}
    )
    a100_requirements = _campaign_remote_requirements(settings, a100_item)
    a100_request = _job_request(h100, a100_item, remote_requirements=a100_requirements)

    assert h100_request.lane.execution_environment_digest != a100_request.lane.execution_environment_digest
    assert h100_request != a100_request


def test_remote_result_artifact_shape_is_json_serializable(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_fake_client()
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client.AsyncSandboxClient",
        _AcceleratorAsyncClient,
    )
    result = PrimeIntellectClient(
        api_key="test-key",
        docker_image=PINNED_PYTHON_RUNTIME_IMAGE,
        resource_capabilities=_capabilities(),
    ).execute_request(_request(), max_retries=0)

    assert json.loads(json.dumps(asdict(result.to_ledger_entry())))["provenance"]["resolved"]["accelerator_kind"] == "H100"
