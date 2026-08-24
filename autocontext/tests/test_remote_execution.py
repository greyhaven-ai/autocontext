from __future__ import annotations

import base64
import json
import time

import pytest

from autocontext.execution.campaign_scheduler_adapters import campaign_result_from_remote
from autocontext.execution.remote_execution import (
    RemoteAcceleratorRequest,
    RemoteCleanupOutcome,
    RemoteExecutionProvenance,
    RemoteExecutionRequest,
    RemoteExecutionRequirements,
    RemoteExecutionResult,
    RemoteInputArtifact,
    RemoteInputProvenance,
    RemoteResourceRequest,
    RemoteResourceUsage,
    RemoteSecretGrant,
    parse_remote_stdout,
    remote_request_provenance,
    requests_are_reuse_compatible,
)
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE


def _request(**overrides: object) -> RemoteExecutionRequest:
    values: dict[str, object] = {
        "task_id": "research-1",
        "image": "python:3.13",
        "command": "python task.py",
        "expected_outputs": ("report.json",),
    }
    values.update(overrides)
    return RemoteExecutionRequest(**values)  # type: ignore[arg-type]


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


def test_remote_request_rejects_path_escape_expired_secrets_and_implicit_warmth() -> None:
    with pytest.raises(ValueError, match="relative"):
        RemoteInputArtifact("../secret", b"")
    with pytest.raises(ValueError, match="expired"):
        _request(
            secrets_policy="scoped_grants",
            secret_grants=(RemoteSecretGrant("expired", "grant-1", time.time() - 1),),
        )
    with pytest.raises(ValueError, match="snapshot_id"):
        _request(lifecycle="warm_snapshot")


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
    ledger = result.to_ledger_entry()
    assert ledger.infrastructure_succeeded is True
    assert ledger.candidate_succeeded is True


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
