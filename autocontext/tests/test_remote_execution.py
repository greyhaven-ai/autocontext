from __future__ import annotations

import base64
import time

import pytest

from autocontext.execution.campaign_scheduler_adapters import campaign_result_from_remote
from autocontext.execution.remote_execution import (
    RemoteAcceleratorRequest,
    RemoteCleanupOutcome,
    RemoteExecutionRequest,
    RemoteInputArtifact,
    RemoteResourceRequest,
    RemoteResourceUsage,
    RemoteSecretGrant,
    parse_remote_stdout,
    remote_request_provenance,
    requests_are_reuse_compatible,
)


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
