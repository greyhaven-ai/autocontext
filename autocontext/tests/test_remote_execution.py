from __future__ import annotations

import base64
import json
import math
import time
from collections.abc import ItemsView, Iterator, Mapping
from typing import Any

import pytest

import autocontext.execution._external_eval_outbox_codec as _outbox_codec
from autocontext.execution.campaign_scheduler_adapters import campaign_result_from_remote
from autocontext.execution.remote_execution import (
    ExternalEvalLedgerEntry,
    RemoteAcceleratorRequest,
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionProvenance,
    RemoteExecutionRequest,
    RemoteExecutionRequirements,
    RemoteExecutionResult,
    RemoteInputArtifact,
    RemoteInputProvenance,
    RemoteOutputArtifact,
    RemoteResolvedEnvironment,
    RemoteResourceRequest,
    RemoteResourceUsage,
    RemoteSecretGrant,
    parse_remote_stdout,
    remote_request_provenance,
    remote_request_sha256,
    requests_are_reuse_compatible,
)
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE


class _StringChild(str):
    pass


class _BytesChild(bytes):
    pass


class _RequestChild(RemoteExecutionRequest):
    pass


def _request(**overrides: object) -> RemoteExecutionRequest:
    values: dict[str, object] = {
        "task_id": "research-1",
        "image": "python:3.13",
        "command": "python task.py",
        "expected_outputs": ("report.json",),
    }
    values.update(overrides)
    return RemoteExecutionRequest(**values)  # type: ignore[arg-type]


def _subclass_of(model_type: type[Any]) -> type[Any]:
    return type(f"Derived{model_type.__name__}", (model_type,), {})


class _DivergentMapping(Mapping[object, object]):
    """Mapping whose overridden items view disagrees with its mapping protocol."""

    def __init__(
        self,
        protocol_values: dict[object, object],
        reported_items: dict[object, object],
    ) -> None:
        self._protocol_values = protocol_values
        self._reported_items = reported_items

    def __getitem__(self, key: object) -> object:
        return self._protocol_values[key]

    def __iter__(self) -> Iterator[object]:
        return iter(self._protocol_values)

    def __len__(self) -> int:
        return len(self._protocol_values)

    def items(self) -> ItemsView[object, object]:
        return self._reported_items.items()


def test_remote_request_covers_resources_artifacts_network_and_scoped_secrets() -> None:
    request = _request(
        image=PINNED_PYTHON_RUNTIME_IMAGE,
        resources=RemoteResourceRequest(
            cpu_cores=4,
            memory_gb=16,
            disk_gb=40,
            accelerator=RemoteAcceleratorRequest(kind="A100", count=1, memory_gb=80),
        ),
        timeout_seconds=120,
        network_policy="allow",
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", time.time() + 60),),
        input_artifacts=(RemoteInputArtifact("src/task.py", b"print('ok')", "text/x-python"),),
    )

    assert request.resources.accelerator and request.resources.accelerator.kind == "A100"
    assert request.secret_grants[0].grant_id == "grant-1"
    assert request.input_artifacts[0].name == "src/task.py"


def test_accelerator_requirements_reject_mutable_images() -> None:
    resources = RemoteResourceRequest(accelerator=RemoteAcceleratorRequest("H100"))

    with pytest.raises(ValueError, match="immutable @sha256 digest"):
        RemoteExecutionRequirements(image="gpu/runtime:latest", resources=resources)
    with pytest.raises(ValueError, match="immutable @sha256 digest"):
        _request(image="gpu/runtime:latest", resources=resources)


def test_remote_requirements_reject_invalid_resource_object() -> None:
    with pytest.raises(TypeError, match="resources must be a RemoteResourceRequest"):
        RemoteExecutionRequirements(image="python:3.13", resources=object())  # type: ignore[arg-type]


def test_remote_request_preserves_legacy_positional_constructor_order() -> None:
    resources = RemoteResourceRequest(cpu_cores=2, memory_gb=4, disk_gb=8)
    request = RemoteExecutionRequest(
        "task-1",
        "python:3.13",
        "python task.py",
        resources,
        120.0,
        "allow",
        "deny",
        (),
        (),
        ("report.json",),
        "ephemeral_per_eval",
        {"MODE": "test"},
        None,
        2,
        {"seed": "7"},
    )

    assert request.timeout_seconds == 120.0
    assert request.network_policy == "allow"
    assert request.max_reuse_tasks == 2
    assert request.region is None
    assert request.required_telemetry == frozenset()


def test_remote_provenance_preserves_legacy_positional_constructor_order() -> None:
    inputs = (RemoteInputProvenance("input.json", "a" * 64, 2, "application/json"),)
    provenance = RemoteExecutionProvenance(
        "python:3.13",
        "b" * 64,
        "c" * 64,
        inputs,
        7,
        "d" * 64,
        "e" * 64,
        "f" * 64,
    )

    assert provenance.image == "python:3.13"
    assert provenance.inputs == inputs
    assert provenance.fixture_observation_sha256 == "f" * 64
    assert provenance.request_sha256 == ""


def test_remote_request_rejects_path_escape_and_implicit_warmth() -> None:
    with pytest.raises(ValueError, match="relative"):
        RemoteInputArtifact("../secret", b"")
    with pytest.raises(ValueError, match="snapshot_id"):
        _request(lifecycle="warm_snapshot")


def test_remote_request_keeps_expired_grants_reconstructible_for_durable_replay() -> None:
    request = _request(
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("expired", "grant-1", time.time() - 1),),
    )

    assert request.secret_grants[0].name == "expired"


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeout_seconds": float("nan")},
        {"network_policy": "alow"},
        {"secrets_policy": "ambient"},
        {"lifecycle": "reusable"},
        {"environment": {"INVALID-NAME": "value"}},
        {
            "input_artifacts": (
                RemoteInputArtifact("input.json", b"first"),
                RemoteInputArtifact("input.json", b"second"),
            )
        },
        {"expected_outputs": ("report.json", "report.json")},
    ],
)
def test_remote_request_rejects_ambiguous_or_nonfinite_contracts(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _request(**overrides)


def test_remote_resources_reject_nonfinite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        RemoteResourceRequest(cpu_cores=float("nan"))
    with pytest.raises(ValueError, match="accelerator memory"):
        RemoteAcceleratorRequest("A100", memory_gb=float("inf"))


def test_remote_request_normalizes_numeric_identity_fields() -> None:
    integer_request = _request(
        image=PINNED_PYTHON_RUNTIME_IMAGE,
        resources=RemoteResourceRequest(
            cpu_cores=2,
            memory_gb=8,
            disk_gb=20,
            accelerator=RemoteAcceleratorRequest("H100", memory_gb=80),
        ),
        timeout_seconds=120,
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", 32_503_680_000),),
    )
    float_request = _request(
        image=PINNED_PYTHON_RUNTIME_IMAGE,
        resources=RemoteResourceRequest(
            cpu_cores=2.0,
            memory_gb=8.0,
            disk_gb=20.0,
            accelerator=RemoteAcceleratorRequest("H100", memory_gb=80.0),
        ),
        timeout_seconds=120.0,
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", 32_503_680_000.0),),
    )

    assert integer_request == float_request
    assert remote_request_sha256(integer_request) == remote_request_sha256(float_request)
    assert type(integer_request.timeout_seconds) is float
    assert type(integer_request.resources.cpu_cores) is float
    assert type(integer_request.resources.memory_gb) is float
    assert type(integer_request.resources.disk_gb) is float
    assert integer_request.resources.accelerator is not None
    assert type(integer_request.resources.accelerator.memory_gb) is float
    assert type(integer_request.secret_grants[0].expires_at) is float

    negative_zero = _request(
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", -0.0),),
    )
    positive_zero = _request(
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", 0.0),),
    )
    assert negative_zero == positive_zero
    assert math.copysign(1.0, negative_zero.secret_grants[0].expires_at) == 1.0
    assert remote_request_sha256(negative_zero) == remote_request_sha256(positive_zero)


def test_remote_request_snapshots_caller_owned_identity_sequences() -> None:
    grants = [RemoteSecretGrant("dataset", "grant-1", 32_503_680_000)]
    inputs = [RemoteInputArtifact("input.json", b"{}", "application/json")]
    outputs = ["report.json"]
    request = _request(
        secrets_policy="scoped_grants",
        secret_grants=grants,
        input_artifacts=inputs,
        expected_outputs=outputs,
    )
    original_digest = remote_request_sha256(request)

    grants.append(RemoteSecretGrant("other", "grant-2", 32_503_680_001))
    inputs.clear()
    outputs[0] = "different.json"

    assert request.secret_grants == (RemoteSecretGrant("dataset", "grant-1", 32_503_680_000),)
    assert request.input_artifacts == (RemoteInputArtifact("input.json", b"{}", "application/json"),)
    assert request.expected_outputs == ("report.json",)
    assert remote_request_sha256(request) == original_digest


def test_strict_task_identity_is_validated_but_excluded_from_remote_request_hash() -> None:
    ordinary = _request()
    strict = _request(strict_task_identity=True)

    assert remote_request_sha256(strict) == remote_request_sha256(ordinary)
    with pytest.raises(TypeError, match="strict_task_identity must be boolean"):
        _request(strict_task_identity=1)


def test_remote_result_and_provenance_snapshot_caller_owned_sequences() -> None:
    inputs = [RemoteInputProvenance("input.json", "a" * 64, 2, "application/json")]
    telemetry = ["hardware_identity"]
    provenance = RemoteExecutionProvenance(inputs=inputs, required_telemetry=telemetry)
    artifacts = [RemoteOutputArtifact("report.json", b"{}", "application/json")]
    events = [RemoteExecutionEvent(sequence=1, event_type="provider")]
    result = RemoteExecutionResult(
        task_id="immutable-result",
        provider="test",
        status="success",
        artifacts=artifacts,
        events=events,
        provenance=provenance,
    )

    inputs.clear()
    telemetry.append("accelerator_usage")
    artifacts.clear()
    events.clear()

    assert len(provenance.inputs) == 1
    assert provenance.required_telemetry == ("hardware_identity",)
    assert len(result.artifacts) == 1
    assert len(result.events) == 1


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda: RemoteResourceUsage(wall_seconds=True), id="usage-bool"),
        pytest.param(lambda: RemoteResourceUsage(wall_seconds=object()), id="usage-non-builtin"),
        pytest.param(lambda: RemoteResourceUsage(wall_seconds=float("inf")), id="usage-nonfinite"),
        pytest.param(lambda: RemoteResourceUsage(wall_seconds=2**53 + 1), id="usage-rounded-int"),
        pytest.param(lambda: RemoteResourceUsage(wall_seconds=10**400), id="usage-overflow"),
        pytest.param(lambda: RemoteResourceUsage(cpu_seconds=-1), id="usage-negative"),
        pytest.param(lambda: RemoteExecutionEvent(sequence=-1, event_type="provider"), id="event-sequence"),
        pytest.param(
            lambda: RemoteExecutionEvent(sequence=1, event_type="provider", fields={_StringChild("key"): 1}),
            id="event-key-subclass",
        ),
        pytest.param(lambda: RemoteOutputArtifact(_StringChild("output"), b"x"), id="artifact-name-subclass"),
        pytest.param(lambda: RemoteOutputArtifact("output", _BytesChild(b"x")), id="artifact-bytes-subclass"),
        pytest.param(
            lambda: RemoteOutputArtifact("output", b"x", _StringChild("text/plain")),
            id="artifact-media-subclass",
        ),
        pytest.param(lambda: RemoteInputProvenance("", "a" * 64, 1, "text/plain"), id="input-name"),
        pytest.param(lambda: RemoteInputProvenance("input", "A" * 64, 1, "text/plain"), id="input-sha"),
        pytest.param(lambda: RemoteInputProvenance("input", "a" * 64, True, "text/plain"), id="input-size-bool"),
        pytest.param(lambda: RemoteInputProvenance("input", "a" * 64, -1, "text/plain"), id="input-size-negative"),
        pytest.param(lambda: RemoteInputProvenance("input", "a" * 64, 1, " "), id="input-media"),
        pytest.param(lambda: RemoteResolvedEnvironment(image=object()), id="resolved-string"),
        pytest.param(lambda: RemoteResolvedEnvironment(accelerator_count=True), id="resolved-count-bool"),
        pytest.param(lambda: RemoteResolvedEnvironment(accelerator_count=-1), id="resolved-count-negative"),
        pytest.param(lambda: RemoteExecutionProvenance(image=object()), id="provenance-string"),
        pytest.param(lambda: RemoteExecutionProvenance(seed=True), id="provenance-seed-bool"),
        pytest.param(
            lambda: RemoteExecutionProvenance(requested_accelerator_count=True),
            id="provenance-count-bool",
        ),
        pytest.param(
            lambda: RemoteExecutionProvenance(requested_accelerator_count=-1),
            id="provenance-count-negative",
        ),
        pytest.param(
            lambda: RemoteExecutionProvenance(requested_accelerator_memory_gb=True),
            id="provenance-memory-bool",
        ),
        pytest.param(
            lambda: RemoteExecutionProvenance(requested_accelerator_memory_gb=float("nan")),
            id="provenance-memory-nonfinite",
        ),
        pytest.param(
            lambda: RemoteExecutionProvenance(requested_accelerator_memory_gb=0),
            id="provenance-memory-nonpositive",
        ),
        pytest.param(
            lambda: RemoteExecutionProvenance(required_telemetry=(" ",)),
            id="provenance-telemetry",
        ),
        pytest.param(lambda: RemoteExecutionProvenance(inputs=(object(),)), id="provenance-inputs"),
        pytest.param(lambda: RemoteExecutionProvenance(resolved=object()), id="provenance-resolved"),
        pytest.param(lambda: RemoteCleanupOutcome(attempted=1, succeeded=True), id="cleanup-attempted"),
        pytest.param(
            lambda: RemoteCleanupOutcome(attempted=True, succeeded=True, resource_id=object()),
            id="cleanup-resource",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id=" ", provider="fake", status="success"),
            id="result-task",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider=" ", status="success"),
            id="result-provider",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider="fake", status="unknown"),
            id="result-status",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider="fake", status="success", exit_code=True),
            id="result-exit-code",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider="fake", status="success", stdout=object()),
            id="result-stdout",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider="fake", status="success", usage=object()),
            id="result-usage",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider="fake", status="success", cleanup=object()),
            id="result-cleanup",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider="fake", status="success", provenance=object()),
            id="result-provenance",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider="fake", status="success", events=(object(),)),
            id="result-events",
        ),
        pytest.param(
            lambda: ExternalEvalLedgerEntry(
                task_id="task",
                provider="fake",
                status="success",
                candidate_succeeded=1,
                infrastructure_succeeded=True,
                exit_code=0,
                usage=RemoteResourceUsage(),
                cleanup=RemoteCleanupOutcome(True, True),
            ),
            id="ledger-candidate-success",
        ),
        pytest.param(
            lambda: ExternalEvalLedgerEntry(
                task_id="task",
                provider="fake",
                status="success",
                candidate_succeeded=True,
                infrastructure_succeeded=True,
                exit_code=0,
                usage=RemoteResourceUsage(),
                cleanup=RemoteCleanupOutcome(True, True),
                attempt_id=object(),
            ),
            id="ledger-attempt-type",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(task_id="task", provider="fake", status="success").to_ledger_entry(
                attempt_id="attempt-1"
            ),
            id="ledger-attempt-format",
        ),
    ],
)
def test_durable_remote_models_reject_values_that_cannot_be_strictly_encoded(factory: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(
            lambda: RemoteResourceRequest(accelerator=_subclass_of(RemoteAcceleratorRequest)("H100")),
            id="request-accelerator",
        ),
        pytest.param(
            lambda: RemoteExecutionRequirements(image="python:3.13", resources=_subclass_of(RemoteResourceRequest)()),
            id="requirements-resources",
        ),
        pytest.param(
            lambda: _request(resources=_subclass_of(RemoteResourceRequest)()),
            id="request-resources",
        ),
        pytest.param(
            lambda: _request(
                secrets_policy="scoped_grants",
                secret_grants=(_subclass_of(RemoteSecretGrant)("secret", "grant", 1.0),),
            ),
            id="request-secret-grants",
        ),
        pytest.param(
            lambda: _request(input_artifacts=(_subclass_of(RemoteInputArtifact)("input", b""),)),
            id="request-input-artifacts",
        ),
        pytest.param(
            lambda: RemoteExecutionProvenance(inputs=(_subclass_of(RemoteInputProvenance)("input", "a" * 64, 0, "text/plain"),)),
            id="provenance-inputs",
        ),
        pytest.param(
            lambda: RemoteExecutionProvenance(resolved=_subclass_of(RemoteResolvedEnvironment)()),
            id="provenance-resolved",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(
                task_id="task",
                provider="fake",
                status="success",
                artifacts=(_subclass_of(RemoteOutputArtifact)("output", b""),),
            ),
            id="result-artifacts",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(
                task_id="task",
                provider="fake",
                status="success",
                events=(_subclass_of(RemoteExecutionEvent)(0, "provider"),),
            ),
            id="result-events",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(
                task_id="task",
                provider="fake",
                status="success",
                usage=_subclass_of(RemoteResourceUsage)(),
            ),
            id="result-usage",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(
                task_id="task",
                provider="fake",
                status="success",
                cleanup=_subclass_of(RemoteCleanupOutcome)(False, False),
            ),
            id="result-cleanup",
        ),
        pytest.param(
            lambda: RemoteExecutionResult(
                task_id="task",
                provider="fake",
                status="success",
                provenance=_subclass_of(RemoteExecutionProvenance)(),
            ),
            id="result-provenance",
        ),
        pytest.param(
            lambda: _subclass_of(RemoteExecutionResult)(task_id="task", provider="fake", status="success"),
            id="result-top-level",
        ),
        pytest.param(
            lambda: ExternalEvalLedgerEntry(
                task_id="task",
                provider="fake",
                status="success",
                candidate_succeeded=True,
                infrastructure_succeeded=True,
                exit_code=0,
                usage=_subclass_of(RemoteResourceUsage)(),
                cleanup=RemoteCleanupOutcome(True, True),
            ),
            id="ledger-usage",
        ),
        pytest.param(
            lambda: ExternalEvalLedgerEntry(
                task_id="task",
                provider="fake",
                status="success",
                candidate_succeeded=True,
                infrastructure_succeeded=True,
                exit_code=0,
                usage=RemoteResourceUsage(),
                cleanup=_subclass_of(RemoteCleanupOutcome)(True, True),
            ),
            id="ledger-cleanup",
        ),
        pytest.param(
            lambda: ExternalEvalLedgerEntry(
                task_id="task",
                provider="fake",
                status="success",
                candidate_succeeded=True,
                infrastructure_succeeded=True,
                exit_code=0,
                usage=RemoteResourceUsage(),
                cleanup=RemoteCleanupOutcome(True, True),
                provenance=_subclass_of(RemoteExecutionProvenance)(),
            ),
            id="ledger-provenance",
        ),
        pytest.param(
            lambda: _subclass_of(ExternalEvalLedgerEntry)(
                task_id="task",
                provider="fake",
                status="success",
                candidate_succeeded=True,
                infrastructure_succeeded=True,
                exit_code=0,
                usage=RemoteResourceUsage(),
                cleanup=RemoteCleanupOutcome(True, True),
            ),
            id="ledger-top-level",
        ),
    ],
)
def test_durable_models_reject_dataclass_subclasses_that_the_codec_cannot_preserve(factory: object) -> None:
    with pytest.raises(TypeError):
        factory()  # type: ignore[operator]


def test_valid_durable_result_model_roundtrips_without_type_drift() -> None:
    request = _request(
        image=PINNED_PYTHON_RUNTIME_IMAGE,
        resources=RemoteResourceRequest(
            accelerator=RemoteAcceleratorRequest("H100", memory_gb=80),
        ),
        input_artifacts=(RemoteInputArtifact("input.json", b"{}", "application/json"),),
    )
    result = RemoteExecutionResult(
        task_id=request.task_id,
        provider="fake",
        status="success",
        stdout="{}",
        exit_code=0,
        artifacts=(RemoteOutputArtifact("report.json", b"{}", "application/json"),),
        events=(RemoteExecutionEvent(sequence=0, event_type="provider", fields={"attempts": [1]}),),
        usage=RemoteResourceUsage(
            wall_seconds=2,
            cpu_seconds=1,
            peak_memory_mb=128,
            accelerator_seconds=0,
            accelerator_peak_memory_mb=64,
        ),
        cleanup=RemoteCleanupOutcome(True, True, "sandbox-1"),
        provenance=remote_request_provenance(
            request,
            resolved=RemoteResolvedEnvironment(
                image="python:3.13",
                region="us-central-1",
                accelerator_kind="H100",
                accelerator_count=1,
                runtime="python-3.13",
            ),
        ),
    )

    decoded = _outbox_codec.result_from_payload(_outbox_codec.result_payload(result))
    ledger = result.to_ledger_entry(attempt_id="a" * 64)
    decoded_ledger = _outbox_codec.ledger_from_payload(_outbox_codec.ledger_payload(ledger))

    assert decoded == result
    assert decoded_ledger == ledger
    assert result.to_ledger_entry().attempt_id == ""
    assert type(result.usage.wall_seconds) is float
    assert type(result.usage.accelerator_seconds) is float
    assert type(result.provenance.requested_accelerator_memory_gb) is float


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"secret_grants": [object()]}, "secret_grants must contain"),
        ({"input_artifacts": [object()]}, "input_artifacts must contain"),
        ({"expected_outputs": [object()]}, "expected_outputs must contain"),
        ({"expected_outputs": "report.json"}, "expected_outputs must be a sequence"),
        ({"snapshot_id": {}}, "snapshot_id must be a string"),
    ],
)
def test_remote_request_rejects_invalid_identity_sequence_elements(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        _request(**overrides)


def test_remote_request_rejects_empty_optional_snapshot_id() -> None:
    with pytest.raises(ValueError, match="snapshot_id must be non-empty"):
        _request(snapshot_id=" ")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RemoteResourceRequest(cpu_cores=True),
        lambda: RemoteResourceRequest(memory_gb=True),
        lambda: RemoteResourceRequest(disk_gb=True),
        lambda: RemoteAcceleratorRequest("H100", memory_gb=True),
        lambda: RemoteAcceleratorRequest(1),
        lambda: RemoteSecretGrant("dataset", "grant-1", True),
        lambda: RemoteSecretGrant(1, "grant-1", 1.0),
        lambda: _request(timeout_seconds=True),
        lambda: _request(max_reuse_tasks=True),
        lambda: _request(max_reuse_tasks=1.0),
    ],
)
def test_remote_request_rejects_noncanonical_numeric_types(factory: object) -> None:
    with pytest.raises(TypeError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _RequestChild(task_id="task", image="python:3.13", command="true"),
        lambda: _request(task_id=_StringChild("task")),
        lambda: _request(image=_StringChild("python:3.13")),
        lambda: _request(command=_StringChild("true")),
        lambda: RemoteAcceleratorRequest(_StringChild("H100")),
        lambda: RemoteSecretGrant(_StringChild("dataset"), "grant-1", 1.0),
        lambda: RemoteSecretGrant("dataset", _StringChild("grant-1"), 1.0),
        lambda: RemoteInputArtifact(_StringChild("input"), b"x"),
        lambda: RemoteInputArtifact("input", _BytesChild(b"x")),
        lambda: RemoteInputArtifact("input", b"x", _StringChild("text/plain")),
        lambda: _request(network_policy=_StringChild("deny")),
        lambda: _request(secrets_policy=_StringChild("deny")),
        lambda: _request(lifecycle=_StringChild("ephemeral_per_eval")),
        lambda: _request(snapshot_id=_StringChild("snapshot-1")),
        lambda: _request(environment={_StringChild("NAME"): "value"}),
        lambda: _request(environment={"NAME": _StringChild("value")}),
        lambda: _request(metadata={_StringChild("name"): "value"}),
        lambda: _request(metadata={"name": _StringChild("value")}),
        lambda: RemoteExecutionRequirements(
            image="python:3.13",
            required_telemetry=frozenset({_StringChild("hardware_identity")}),
        ),
    ],
)
def test_remote_request_rejects_noncanonical_string_and_byte_subclasses(factory: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RemoteResourceRequest(cpu_cores=2**53 + 1),
        lambda: RemoteAcceleratorRequest("H100", memory_gb=2**53 + 1),
        lambda: RemoteSecretGrant("dataset", "grant-1", 2**53 + 1),
        lambda: _request(timeout_seconds=2**53 + 1),
    ],
)
def test_remote_request_rejects_integer_float_identity_aliases(factory: object) -> None:
    with pytest.raises(ValueError, match="exactly representable"):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize("count", [True, 1.5])
def test_remote_accelerator_count_must_be_an_integer(count: object) -> None:
    with pytest.raises(TypeError, match="accelerator count must be an integer"):
        RemoteAcceleratorRequest("H100", count=count)  # type: ignore[arg-type]


def test_prepared_fixture_provenance_is_complete_valid_and_preserved() -> None:
    metadata = {
        "fixture_digest": "a" * 64,
        "fixture_state_sha256": "b" * 64,
        "fixture_observation_sha256": "c" * 64,
    }

    provenance = remote_request_provenance(_request(metadata=metadata))

    assert provenance.fixture_digest == "a" * 64
    assert provenance.fixture_state_sha256 == "b" * 64
    assert provenance.fixture_observation_sha256 == "c" * 64


@pytest.mark.parametrize(
    "metadata",
    [
        {"fixture_digest": "a" * 64},
        {
            "fixture_digest": "a" * 64,
            "fixture_state_sha256": "b" * 64,
            "fixture_observation_sha256": "not-a-digest",
        },
    ],
)
def test_prepared_fixture_provenance_fails_closed_when_incomplete_or_invalid(
    metadata: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="prepared fixture provenance"):
        _request(metadata=metadata)


def test_remote_request_snapshots_mutable_environment_and_metadata() -> None:
    environment = {"DATASET": "first"}
    metadata = {"seed": "7"}
    request = _request(environment=environment, metadata=metadata)

    environment["DATASET"] = "second"
    metadata["seed"] = "8"

    assert dict(request.environment) == {"DATASET": "first"}
    assert dict(request.metadata) == {"seed": "7"}


def test_remote_request_validates_the_exact_mapping_snapshot_it_retains() -> None:
    safe_items = {"SAFE_NAME": "validated"}

    with pytest.raises(ValueError, match="remote environment names"):
        _request(
            environment=_DivergentMapping(
                {"BAD-NAME": 7},
                safe_items,
            )
        )
    with pytest.raises(ValueError, match="remote metadata names"):
        _request(
            metadata=_DivergentMapping(
                {"seed": object()},
                safe_items,
            )
        )


def test_remote_event_freezes_and_validates_one_mapping_snapshot() -> None:
    fields = _DivergentMapping(
        {"safe": {"nested": [1]}},
        {7: {"nested": [2]}},
    )

    event = RemoteExecutionEvent(sequence=1, event_type="provider", fields=fields)

    assert dict(event.fields) == {"safe": {"nested": (1,)}}

    with pytest.raises(ValueError, match="event type must be non-empty"):
        RemoteExecutionEvent(sequence=2, event_type="  ")


def test_parser_streams_events_and_collects_declared_artifacts() -> None:
    encoded = base64.b64encode(b'{"score": 0.9}').decode()
    stdout = (
        '{"type":"event","event":"progress","message":"halfway","percent":50}\n'
        f'{{"artifacts":{{"report.json":{{"base64":"{encoded}","media_type":"application/json"}}}}}}\n'
    )

    result = parse_remote_stdout(
        _request(),
        provider="fake",
        stdout=stdout,
        stderr="",
        exit_code=0,
        usage=RemoteResourceUsage(wall_seconds=2.5),
        cleanup=RemoteCleanupOutcome(True, True, "sandbox-1"),
        session_id="sandbox-1",
    )

    assert result.status == "success"
    assert result.events[0].event_type == "progress"
    assert result.artifact("report.json") and result.artifact("report.json").content == b'{"score": 0.9}'
    ledger = result.to_ledger_entry(attempt_id="a" * 64)
    assert ledger.infrastructure_succeeded is True
    assert ledger.candidate_succeeded is True
    assert ledger.attempt_id == "a" * 64


@pytest.mark.parametrize(
    ("exit_code", "expected_outputs", "cleanup", "expected_status"),
    [
        (2, (), RemoteCleanupOutcome(True, True), "task_error"),
        (0, ("missing.txt",), RemoteCleanupOutcome(True, True), "artifact_error"),
        (0, (), RemoteCleanupOutcome(True, False, detail="delete failed"), "cleanup_error"),
    ],
)
def test_parser_keeps_task_artifact_and_cleanup_failures_distinct(
    exit_code: int,
    expected_outputs: tuple[str, ...],
    cleanup: RemoteCleanupOutcome,
    expected_status: str,
) -> None:
    result = parse_remote_stdout(
        _request(expected_outputs=expected_outputs),
        provider="fake",
        stdout="{}",
        stderr="candidate failed" if exit_code else "",
        exit_code=exit_code,
        usage=RemoteResourceUsage(),
        cleanup=cleanup,
        session_id="sandbox-1",
    )

    assert result.status == expected_status


def test_cleanup_failure_takes_infrastructure_precedence_over_task_failure() -> None:
    result = parse_remote_stdout(
        _request(expected_outputs=()),
        provider="fake",
        stdout="{}",
        stderr="candidate failed",
        exit_code=2,
        usage=RemoteResourceUsage(),
        cleanup=RemoteCleanupOutcome(True, False, "sandbox-1", "delete failed"),
        session_id="sandbox-1",
    )

    assert result.status == "cleanup_error"
    assert "candidate failed" in result.error
    assert "delete failed" in result.error
    assert result.to_ledger_entry().infrastructure_succeeded is False


def test_declared_bootstrap_failure_is_typed_as_infrastructure() -> None:
    result = parse_remote_stdout(
        _request(expected_outputs=(), metadata={"bootstrap_exit_code": "70"}),
        provider="fake",
        stdout="",
        stderr='{"autocontext_bootstrap_error":"digest mismatch"}',
        exit_code=70,
        usage=RemoteResourceUsage(),
        cleanup=RemoteCleanupOutcome(True, True, "sandbox-1"),
        session_id="sandbox-1",
    )

    assert result.status == "artifact_error"
    assert result.to_ledger_entry().infrastructure_succeeded is False
    assert campaign_result_from_remote(result).outcome == "infrastructure_failure"


def test_remote_retryability_requires_verified_cleanup() -> None:
    with pytest.raises(ValueError, match="verified cleanup"):
        RemoteExecutionResult(
            task_id="unsafe-retry",
            provider="fake",
            status="provider_error",
            cleanup=RemoteCleanupOutcome(attempted=False, succeeded=True),
            retryable=True,
        )


@pytest.mark.parametrize(
    "payload_update",
    [
        {"result": {"score": 1.0, "summary": "ok", "replay": [1], "metrics": {}, "validation_errors": []}},
        {"replay": {"scenario": "othello", "seed": 7, "narrative": "ok", "timeline": [1]}},
    ],
)
def test_scenario_parser_rejects_payloads_that_exact_result_models_reject_before_ledger(
    payload_update: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "result": {"score": 1.0, "summary": "ok", "replay": [], "metrics": {}, "validation_errors": []},
        "replay": {"scenario": "othello", "seed": 7, "narrative": "ok", "timeline": []},
    }
    payload.update(payload_update)
    request = _request(
        expected_outputs=(),
        metadata={"task_kind": "scenario_match", "scenario": "othello", "seed": "7"},
    )

    result = parse_remote_stdout(
        request,
        provider="fake",
        stdout=json.dumps(payload),
        stderr="",
        exit_code=0,
        usage=RemoteResourceUsage(),
        cleanup=RemoteCleanupOutcome(True, True, "sandbox-1"),
        session_id="sandbox-1",
    )

    assert result.status == "artifact_error"
    assert "typed validation" in result.error
    assert result.to_ledger_entry().candidate_succeeded is False
    assert result.to_ledger_entry().infrastructure_succeeded is False


@pytest.mark.parametrize("returned_fixture_digest", [None, "d" * 64])
def test_scenario_parser_verifies_prepared_fixture_attestation_before_ledger(
    returned_fixture_digest: str | None,
) -> None:
    expected_fixture_digest = "a" * 64
    payload: dict[str, object] = {
        "result": {"score": 1.0, "summary": "ok", "replay": [], "metrics": {}, "validation_errors": []},
        "replay": {"scenario": "othello", "seed": 7, "narrative": "ok", "timeline": []},
    }
    if returned_fixture_digest is not None:
        payload["fixture_digest"] = returned_fixture_digest
    request = _request(
        expected_outputs=(),
        metadata={
            "task_kind": "scenario_match",
            "scenario": "othello",
            "seed": "7",
            "fixture_digest": expected_fixture_digest,
            "fixture_state_sha256": "b" * 64,
            "fixture_observation_sha256": "c" * 64,
        },
    )

    result = parse_remote_stdout(
        request,
        provider="fake",
        stdout=json.dumps(payload),
        stderr="",
        exit_code=0,
        usage=RemoteResourceUsage(),
        cleanup=RemoteCleanupOutcome(True, True, "sandbox-1"),
        session_id="sandbox-1",
    )

    assert result.status == "artifact_error"
    assert "prepared fixture attestation mismatch" in result.error
    assert result.to_ledger_entry().candidate_succeeded is False


def test_reuse_requires_a_bounded_equivalent_matched_lane() -> None:
    first = _request(
        task_id="trial-a",
        expected_outputs=(),
        lifecycle="reuse_matched_trials",
        max_reuse_tasks=2,
        metadata={"seed": "7", "evaluator_epoch": "epoch-1"},
    )
    second = _request(
        task_id="trial-b",
        expected_outputs=(),
        lifecycle="reuse_matched_trials",
        max_reuse_tasks=2,
        metadata={"seed": "7", "evaluator_epoch": "epoch-1"},
    )

    assert requests_are_reuse_compatible((first, second)) is True
    assert requests_are_reuse_compatible((first, _request(task_id="cold", expected_outputs=()))) is False


@pytest.mark.parametrize(
    ("first_overrides", "second_overrides"),
    [
        (
            {
                "secrets_policy": "scoped_grants",
                "secret_grants": (RemoteSecretGrant("dataset", "grant-a", 32_503_680_000.0),),
            },
            {
                "secrets_policy": "scoped_grants",
                "secret_grants": (RemoteSecretGrant("dataset", "grant-b", 32_503_680_000.0),),
            },
        ),
        (
            {"input_artifacts": (RemoteInputArtifact("input.json", b"first"),)},
            {"input_artifacts": (RemoteInputArtifact("input.json", b"second"),)},
        ),
        ({"environment": {"DATASET": "first"}}, {"environment": {"DATASET": "second"}}),
        ({"snapshot_id": "snapshot-a"}, {"snapshot_id": "snapshot-b"}),
    ],
)
def test_reuse_rejects_mismatched_provisioned_state(
    first_overrides: dict[str, object],
    second_overrides: dict[str, object],
) -> None:
    common = {"expected_outputs": (), "lifecycle": "reuse_matched_trials", "max_reuse_tasks": 2}
    first = _request(task_id="trial-a", **common, **first_overrides)
    second = _request(task_id="trial-b", **common, **second_overrides)

    assert requests_are_reuse_compatible((first, second)) is False


def test_reuse_allows_per_task_metadata_to_differ() -> None:
    common = {"expected_outputs": (), "lifecycle": "reuse_matched_trials", "max_reuse_tasks": 2}
    first = _request(task_id="trial-a", metadata={"seed": "1"}, **common)
    second = _request(task_id="trial-b", metadata={"seed": "2"}, **common)

    assert requests_are_reuse_compatible((first, second)) is True
