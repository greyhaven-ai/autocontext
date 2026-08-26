from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import autocontext.execution._external_eval_outbox_store as _outbox_store
from autocontext.config.settings import AppSettings
from autocontext.execution import RemoteExecutionAccountingError
from autocontext.execution.external_eval_outbox import (
    ExternalEvalLedgerOutbox,
    ExternalEvalOutboxConflictError,
    ExternalEvalOutboxPendingError,
    ExternalEvalSinkDeliveryPendingError,
    ExternalEvalSinkDeliveryReservation,
    external_eval_attempt_id,
)
from autocontext.execution.remote_execution import (
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteInputArtifact,
    RemoteOutputArtifact,
    RemoteResolvedEnvironment,
    RemoteResourceRequest,
    RemoteResourceUsage,
    RemoteSecretGrant,
    parse_remote_stdout,
    remote_request_provenance,
    remote_request_sha256,
)
from autocontext.execution.runtime_factory import build_execution_runtime
from autocontext.integrations.primeintellect.client import PrimeIntellectClient
from autocontext.offline import OfflineError


def _request(task_id: str = "paid-eval") -> RemoteExecutionRequest:
    return RemoteExecutionRequest(
        task_id=task_id,
        image="python:3.13",
        command="python task.py",
        metadata={"campaign": "release-test"},
    )


def test_outbox_attempt_identity_rejects_provider_string_subclasses() -> None:
    class _ProviderChild(str):
        pass

    with pytest.raises(TypeError, match="provider must be a string"):
        external_eval_attempt_id(_ProviderChild("primeintellect"), _request())


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
    reservation = first.reserve_sink_delivery(claim.attempt_id)
    assert reservation is not None
    assert reservation.attempt_id == claim.attempt_id
    assert reservation.entry == result.to_ledger_entry(attempt_id=claim.attempt_id)
    first.mark_sink_delivered(reservation)

    reopened = ExternalEvalLedgerOutbox(path)
    replay = reopened.claim("primeintellect", request)

    assert replay.result == result
    assert replay.sink_delivered is True
    assert reopened.ledger_entries() == (result.to_ledger_entry(attempt_id=claim.attempt_id),)
    assert reopened.committed_results() == (result,)
    assert reopened.statuses(unresolved_only=True) == ()
    assert path.stat().st_mode & 0o777 == 0o600
    assert reopened.instance_id == first.instance_id
    assert len(first.instance_id) == 64
    assert all(character in "0123456789abcdef" for character in first.instance_id)

    moved_path = tmp_path / "moved-ledger.sqlite3"
    path.replace(moved_path)
    assert ExternalEvalLedgerOutbox(moved_path).instance_id == first.instance_id


@pytest.mark.parametrize("migrate_v2", [False, True])
def test_concurrent_outbox_initializers_share_one_transactional_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    migrate_v2: bool,
) -> None:
    path = tmp_path / "concurrent-instance.sqlite3"
    if migrate_v2:
        ExternalEvalLedgerOutbox(path)
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TABLE remote_eval_outbox_metadata")
            connection.execute(f"PRAGMA user_version={_outbox_store.SCHEMA_VERSION - 1}")

    barrier = threading.Barrier(8)
    token_calls = 0
    token_lock = threading.Lock()

    def token_hex(byte_count: int) -> str:
        nonlocal token_calls
        assert byte_count == 32
        with token_lock:
            token_calls += 1
            return hashlib.sha256(f"instance-{token_calls}".encode()).hexdigest()

    monkeypatch.setattr(_outbox_store.secrets, "token_hex", token_hex)

    def initialize(_: int) -> str:
        barrier.wait(timeout=5)
        return ExternalEvalLedgerOutbox(path).instance_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        instance_ids = tuple(executor.map(initialize, range(8)))

    assert len(set(instance_ids)) == 1
    assert token_calls == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _outbox_store.SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM remote_eval_outbox_metadata").fetchone()[0] == 1


def test_outbox_replays_requests_reconstructed_with_equivalent_numeric_types(tmp_path: Path) -> None:
    integer_request = RemoteExecutionRequest(
        task_id="numeric-replay",
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=1, memory_gb=2, disk_gb=5),
        timeout_seconds=30,
    )
    float_request = RemoteExecutionRequest(
        task_id="numeric-replay",
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=1.0, memory_gb=2.0, disk_gb=5.0),
        timeout_seconds=30.0,
    )
    outbox = ExternalEvalLedgerOutbox(tmp_path / "numeric.sqlite3")
    result = _result(integer_request)
    claim = outbox.claim("primeintellect", integer_request)
    outbox.commit("primeintellect", integer_request, result)

    replay = ExternalEvalLedgerOutbox(outbox.path).replay("primeintellect", float_request)

    assert integer_request == float_request
    assert remote_request_sha256(integer_request) == remote_request_sha256(float_request)
    assert external_eval_attempt_id("primeintellect", float_request) == claim.attempt_id
    assert replay is not None
    assert replay.result == result


def test_strict_task_identity_blocks_changed_request_before_second_provider_dispatch(tmp_path: Path) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "strict-task.sqlite3")
    provider_dispatches = 0

    def execute(request: RemoteExecutionRequest) -> RemoteExecutionResult:
        nonlocal provider_dispatches
        replay = outbox.replay("primeintellect", request)
        if replay is not None:
            assert replay.result is not None
            return replay.result
        outbox.claim("primeintellect", request)
        provider_dispatches += 1
        result = _result(request)
        outbox.commit("primeintellect", request, result, sink_required=False)
        return result

    first = RemoteExecutionRequest(
        task_id="exclusive-context-arm",
        image="python:3.13",
        command="python first.py",
        strict_task_identity=True,
    )
    changed = RemoteExecutionRequest(
        task_id="exclusive-context-arm",
        image="python:3.13",
        command="python regenerated.py",
        strict_task_identity=True,
    )

    execute(first)
    with pytest.raises(ExternalEvalOutboxConflictError, match="already bound to a different durable request"):
        execute(changed)

    assert provider_dispatches == 1
    assert len(outbox.statuses()) == 1


def test_empty_provider_event_name_normalizes_before_durable_commit(tmp_path: Path) -> None:
    request = _request("empty-event-name")
    result = parse_remote_stdout(
        request,
        provider="primeintellect",
        stdout='{"type":"event","event":""}\n{}\n',
        stderr="",
        exit_code=0,
        usage=RemoteResourceUsage(),
        cleanup=RemoteCleanupOutcome(attempted=True, succeeded=True),
        session_id="sandbox-empty-event",
    )
    outbox = ExternalEvalLedgerOutbox(tmp_path / "empty-event.sqlite3")

    outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, result, sink_required=False)
    replay = outbox.replay("primeintellect", request)

    assert result.events[0].event_type == "message"
    assert replay is not None
    assert replay.result == result
    assert outbox.statuses()[0].state == "completed"


def test_outbox_request_identity_is_stable_after_caller_list_mutation(tmp_path: Path) -> None:
    grants = [RemoteSecretGrant("dataset", "grant-1", 32_503_680_000)]
    inputs = [RemoteInputArtifact("input.json", b"{}", "application/json")]
    outputs = ["report.json"]
    request = RemoteExecutionRequest(
        task_id="stable-sequences",
        image="python:3.13",
        command="python task.py",
        secrets_policy="scoped_grants",
        secret_grants=grants,
        input_artifacts=inputs,
        expected_outputs=outputs,
    )
    outbox = ExternalEvalLedgerOutbox(tmp_path / "stable-sequences.sqlite3")
    claim = outbox.claim("primeintellect", request)

    grants.clear()
    inputs.append(RemoteInputArtifact("other.json", b"{}", "application/json"))
    outputs[0] = "different.json"

    assert external_eval_attempt_id("primeintellect", request) == claim.attempt_id
    result = _result(request)
    assert outbox.commit("primeintellect", request, result, sink_required=False) == claim.attempt_id
    replay = outbox.replay("primeintellect", request)
    assert replay is not None
    assert replay.result == result


def test_outbox_snapshots_nested_event_fields_and_result_sequences(tmp_path: Path) -> None:
    fields = {"nested": [1]}
    events = [RemoteExecutionEvent(sequence=1, event_type="provider", fields=fields)]
    request = _request("immutable-result")
    result = replace(_result(request), events=events)
    outbox = ExternalEvalLedgerOutbox(tmp_path / "immutable-result.sqlite3")
    outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, result, sink_required=False)

    fields["nested"].append(2)
    events.clear()

    assert result.events[0].fields["nested"] == (1,)
    with pytest.raises(TypeError):
        result.events[0].fields["other"] = 2  # type: ignore[index]
    replay = outbox.replay("primeintellect", request)
    assert replay is not None
    assert replay.result == result
    assert replay.result.events[0].fields["nested"] == (1,)


def test_schema_v1_integer_numeric_identity_replays_after_float_normalization(tmp_path: Path) -> None:
    path = tmp_path / "legacy-integer.sqlite3"
    legacy_request = RemoteExecutionRequest(
        task_id="legacy-integer-replay",
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=1.0, memory_gb=2.0, disk_gb=5.0),
        timeout_seconds=30.0,
    )
    # Schema v1 predated numeric normalization and retained caller-supplied
    # integer spellings in both its request hash and checksummed request JSON.
    object.__setattr__(legacy_request.resources, "cpu_cores", 1)
    object.__setattr__(legacy_request.resources, "memory_gb", 2)
    object.__setattr__(legacy_request.resources, "disk_gb", 5)
    object.__setattr__(legacy_request, "timeout_seconds", 30)
    legacy_outbox = ExternalEvalLedgerOutbox(path)
    legacy_claim = legacy_outbox.claim("primeintellect", legacy_request)
    legacy_result = _result(legacy_request)
    legacy_outbox.commit("primeintellect", legacy_request, legacy_result, sink_required=False)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=1")

    reconstructed = RemoteExecutionRequest(
        task_id="legacy-integer-replay",
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=1, memory_gb=2, disk_gb=5),
        timeout_seconds=30,
    )
    migrated = ExternalEvalLedgerOutbox(path)

    replay = migrated.replay("primeintellect", reconstructed)

    assert external_eval_attempt_id("primeintellect", reconstructed) != legacy_claim.attempt_id
    assert replay is not None
    assert replay.attempt_id == legacy_claim.attempt_id
    assert replay.result == legacy_result
    assert migrated.claim("primeintellect", reconstructed).attempt_id == legacy_claim.attempt_id
    assert len(migrated.statuses()) == 1


def test_schema_v1_integer_claim_can_be_committed_after_float_normalization(tmp_path: Path) -> None:
    path = tmp_path / "legacy-integer-claim.sqlite3"
    legacy_request = RemoteExecutionRequest(
        task_id="legacy-integer-commit",
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=1.0, memory_gb=2.0, disk_gb=5.0),
        timeout_seconds=30.0,
    )
    object.__setattr__(legacy_request.resources, "cpu_cores", 1)
    object.__setattr__(legacy_request.resources, "memory_gb", 2)
    object.__setattr__(legacy_request.resources, "disk_gb", 5)
    object.__setattr__(legacy_request, "timeout_seconds", 30)
    legacy_outbox = ExternalEvalLedgerOutbox(path)
    legacy_claim = legacy_outbox.claim("primeintellect", legacy_request)
    known_result = _result(legacy_request)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=1")

    reconstructed = RemoteExecutionRequest(
        task_id="legacy-integer-commit",
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=1, memory_gb=2, disk_gb=5),
        timeout_seconds=30,
    )
    migrated = ExternalEvalLedgerOutbox(path)

    committed_attempt = migrated.commit(
        "primeintellect",
        reconstructed,
        known_result,
        sink_required=False,
    )

    assert committed_attempt == legacy_claim.attempt_id
    replay = migrated.replay("primeintellect", reconstructed)
    assert replay is not None
    assert replay.attempt_id == legacy_claim.attempt_id
    assert replay.result == known_result


def test_outbox_fails_closed_when_legacy_and_normalized_identities_both_exist(tmp_path: Path) -> None:
    task_id = "duplicate-numeric-identities"
    legacy_request = RemoteExecutionRequest(
        task_id=task_id,
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=1.0, memory_gb=2.0, disk_gb=5.0),
        timeout_seconds=30.0,
    )
    object.__setattr__(legacy_request.resources, "cpu_cores", 1)
    object.__setattr__(legacy_request.resources, "memory_gb", 2)
    object.__setattr__(legacy_request.resources, "disk_gb", 5)
    object.__setattr__(legacy_request, "timeout_seconds", 30)
    duplicate_path = tmp_path / "duplicate.sqlite3"
    ExternalEvalLedgerOutbox(duplicate_path).claim("primeintellect", legacy_request)

    normalized_request = RemoteExecutionRequest(
        task_id=task_id,
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=1, memory_gb=2, disk_gb=5),
        timeout_seconds=30,
    )
    normalized_path = tmp_path / "normalized.sqlite3"
    ExternalEvalLedgerOutbox(normalized_path).claim("primeintellect", normalized_request)
    with sqlite3.connect(duplicate_path) as connection:
        connection.execute("ATTACH DATABASE ? AS normalized", (str(normalized_path),))
        connection.execute(
            """
            INSERT INTO main.remote_eval_outbox
            SELECT * FROM normalized.remote_eval_outbox
            """
        )

    with pytest.raises(ExternalEvalOutboxConflictError, match="multiple numeric identities"):
        ExternalEvalLedgerOutbox(duplicate_path).replay("primeintellect", normalized_request)


def test_legacy_numeric_lookup_does_not_alias_non_exact_stored_integers(tmp_path: Path) -> None:
    path = tmp_path / "non-exact-integer.sqlite3"
    legacy_request = RemoteExecutionRequest(
        task_id="non-exact-integer",
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=float(2**53), memory_gb=2, disk_gb=5),
    )
    object.__setattr__(legacy_request.resources, "cpu_cores", 2**53 + 1)
    outbox = ExternalEvalLedgerOutbox(path)
    outbox.claim("primeintellect", legacy_request)
    reconstructed = RemoteExecutionRequest(
        task_id="non-exact-integer",
        image="python:3.13",
        command="python task.py",
        resources=RemoteResourceRequest(cpu_cores=2**53, memory_gb=2, disk_gb=5),
    )

    assert outbox.replay("primeintellect", reconstructed) is None
    assert len(outbox.statuses()) == 1


@pytest.mark.parametrize("reuse_bound", [2**53 + 1, 10**400])
def test_legacy_numeric_lookup_does_not_round_or_overflow_large_reuse_bounds(
    tmp_path: Path,
    reuse_bound: int,
) -> None:
    path = tmp_path / f"large-reuse-{len(str(reuse_bound))}.sqlite3"
    legacy_request = _request("large-reuse")
    object.__setattr__(legacy_request, "max_reuse_tasks", 1.0)
    outbox = ExternalEvalLedgerOutbox(path)
    outbox.claim("primeintellect", legacy_request)
    reconstructed = replace(legacy_request, max_reuse_tasks=reuse_bound)

    assert outbox.replay("primeintellect", reconstructed) is None


def test_restart_replays_committed_result_reconstructed_with_expired_grant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    request = RemoteExecutionRequest(
        task_id="expired-grant-replay",
        image="python:3.13",
        command="python task.py",
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", 1.0),),
    )
    result = _result(request)
    original_process = ExternalEvalLedgerOutbox(path)
    original_process.claim("primeintellect", request)
    original_process.commit("primeintellect", request, result)
    reconstructed = RemoteExecutionRequest(
        task_id="expired-grant-replay",
        image="python:3.13",
        command="python task.py",
        secrets_policy="scoped_grants",
        secret_grants=(RemoteSecretGrant("dataset", "grant-1", 1.0),),
    )
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client._prime_sandboxes_sdk",
        lambda: (_ for _ in ()).throw(AssertionError("replay must not require the provider SDK")),
    )

    replayed = PrimeIntellectClient(
        api_key="test-key",
        ledger_outbox=ExternalEvalLedgerOutbox(path),
    ).execute_request(reconstructed, max_retries=0)

    assert replayed == result


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


def test_outbox_rejects_result_with_mismatched_request_derived_provenance(tmp_path: Path) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "provenance.sqlite3")
    request = _request("provenance-mismatch")
    outbox.claim("primeintellect", request)
    result = _result(request)
    mismatched = replace(
        result,
        provenance=replace(result.provenance, image="different:image"),
    )

    with pytest.raises(ExternalEvalOutboxConflictError, match="provenance does not match"):
        outbox.commit("primeintellect", request, mismatched)

    assert outbox.statuses()[0].state == "claimed"


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


def test_outbox_replay_is_read_only_and_validates_existing_rows(tmp_path: Path) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    request = _request("read-only-replay")

    assert outbox.replay("primeintellect", request) is None
    assert outbox.statuses() == ()

    outbox.claim("primeintellect", request)
    with pytest.raises(ExternalEvalOutboxPendingError, match="reconcile provider accounting"):
        outbox.replay("primeintellect", request)

    result = _result(request)
    outbox.commit("primeintellect", request, result)
    replay = outbox.replay("primeintellect", request)

    assert replay is not None
    assert replay.result == result


def test_outbox_resolves_relative_path_once_at_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    monkeypatch.chdir(first_cwd)
    outbox = ExternalEvalLedgerOutbox(Path("runs") / "ledger.sqlite3")

    monkeypatch.chdir(second_cwd)
    outbox.claim("primeintellect", _request("stable-path"))

    assert outbox.path == first_cwd / "runs" / "ledger.sqlite3"
    assert outbox.path.is_file()
    assert not (second_cwd / "runs" / "ledger.sqlite3").exists()


def test_outbox_initializer_rechecks_schema_version_after_acquiring_write_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "schema-race.sqlite3"
    ExternalEvalLedgerOutbox(path)
    original_connect = sqlite3.connect
    with original_connect(path) as connection:
        connection.execute("PRAGMA user_version=1")

    migrator = original_connect(path, timeout=30.0)
    migrator.execute("BEGIN IMMEDIATE")
    begin_attempted = threading.Event()

    class _ObservedConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
            if statement == "BEGIN IMMEDIATE":
                begin_attempted.set()
            return self._connection.execute(statement, parameters)

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

    def observed_connect(*args: object, **kwargs: object) -> _ObservedConnection:
        return _ObservedConnection(original_connect(*args, **kwargs))  # type: ignore[arg-type]

    monkeypatch.setattr(
        "autocontext.execution._external_eval_outbox_store.sqlite3.connect",
        observed_connect,
    )
    errors: list[BaseException] = []

    def initialize() -> None:
        try:
            ExternalEvalLedgerOutbox(path)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=initialize)
    worker.start()
    try:
        assert begin_attempted.wait(timeout=5)
        migrator.execute(f"PRAGMA user_version={_outbox_store.SCHEMA_VERSION + 1}")
        migrator.commit()
    finally:
        if migrator.in_transaction:
            migrator.rollback()
        migrator.close()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert "newer than supported" in str(errors[0])
    with original_connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _outbox_store.SCHEMA_VERSION + 1


def test_outbox_migrates_prerelease_schema_v1_database(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    source = ExternalEvalLedgerOutbox(source_path)
    request = _request("schema-v1")
    claim = source.claim("primeintellect", request)
    result = _result(request)
    source.commit("primeintellect", request, result)
    with sqlite3.connect(source_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM remote_eval_outbox").fetchone()
        assert row is not None

    legacy_ledger = json.loads(row["ledger_json"])
    del legacy_ledger["attempt_id"]
    legacy_ledger_json = json.dumps(
        legacy_ledger,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    legacy_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy_path) as connection:
        connection.executescript(
            """
            CREATE TABLE remote_eval_outbox (
                attempt_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                task_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('claimed', 'completed')),
                result_json TEXT,
                result_sha256 TEXT,
                ledger_json TEXT,
                ledger_sha256 TEXT,
                sink_delivered INTEGER NOT NULL DEFAULT 0 CHECK (sink_delivered IN (0, 1)),
                delivery_error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                completed_at REAL
            );
            PRAGMA user_version=1;
            """
        )
        connection.execute(
            """
            INSERT INTO remote_eval_outbox (
                attempt_id, provider, task_id, request_sha256, request_json, state,
                result_json, result_sha256, ledger_json, ledger_sha256,
                sink_delivered, delivery_error, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["attempt_id"],
                row["provider"],
                row["task_id"],
                row["request_sha256"],
                row["request_json"],
                row["state"],
                row["result_json"],
                row["result_sha256"],
                legacy_ledger_json,
                hashlib.sha256(legacy_ledger_json.encode()).hexdigest(),
                row["sink_delivered"],
                row["delivery_error"],
                row["created_at"],
                row["completed_at"],
            ),
        )

    migrated = ExternalEvalLedgerOutbox(legacy_path)

    replay = migrated.replay("primeintellect", request)
    assert replay is not None
    assert replay.result == result
    assert migrated.ledger_entries() == (result.to_ledger_entry(attempt_id=claim.attempt_id),)
    with sqlite3.connect(legacy_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _outbox_store.SCHEMA_VERSION
        columns = {row[1] for row in connection.execute("PRAGMA table_info(remote_eval_outbox)")}
    assert {"request_json_sha256", "delivery_lease_token", "delivery_lease_expires_at"} <= columns
    assert ExternalEvalLedgerOutbox(legacy_path).instance_id == migrated.instance_id


@pytest.mark.parametrize("corruption", ["missing", "empty", "invalid"])
def test_current_schema_requires_valid_instance_identity_metadata(tmp_path: Path, corruption: str) -> None:
    path = tmp_path / f"invalid-instance-{corruption}.sqlite3"
    ExternalEvalLedgerOutbox(path)
    with sqlite3.connect(path) as connection:
        if corruption == "missing":
            connection.execute("DROP TABLE remote_eval_outbox_metadata")
        elif corruption == "empty":
            connection.execute("DELETE FROM remote_eval_outbox_metadata")
        else:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE remote_eval_outbox_metadata SET instance_id = 'invalid'")

    with pytest.raises(RuntimeError, match="instance identity"):
        ExternalEvalLedgerOutbox(path)


def test_live_outbox_rejects_same_path_database_replacement_before_provider_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    live_outbox = ExternalEvalLedgerOutbox(path)
    replacement_path = tmp_path / "replacement.sqlite3"
    replacement = ExternalEvalLedgerOutbox(replacement_path)
    assert replacement.instance_id != live_outbox.instance_id
    replacement_path.replace(path)
    provider_calls: list[str] = []

    async def execute_provider(
        _client: PrimeIntellectClient,
        request: RemoteExecutionRequest,
        *,
        attempt_number: int,
    ) -> RemoteExecutionResult:
        provider_calls.append(f"{request.task_id}:{attempt_number}")
        return _result(request)

    monkeypatch.setattr(PrimeIntellectClient, "_execute_request_once", execute_provider)
    client = PrimeIntellectClient(api_key="provider-key", ledger_outbox=live_outbox)

    with pytest.raises(RemoteExecutionAccountingError, match="instance identity changed"):
        client.execute_request(_request("replaced-ledger"), max_retries=0)

    assert provider_calls == []


def test_outbox_checksums_request_payload_for_claim_and_statuses(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    outbox = ExternalEvalLedgerOutbox(path)
    request = _request("request-corruption")
    outbox.claim("primeintellect", request)

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE remote_eval_outbox SET request_json = request_json || ' '")

    with pytest.raises(ValueError, match="request checksum mismatch"):
        outbox.statuses()
    with pytest.raises(ValueError, match="request checksum mismatch"):
        outbox.claim("primeintellect", request)


def test_outbox_rejects_validly_checksummed_request_type_corruption(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    outbox = ExternalEvalLedgerOutbox(path)
    outbox.claim("primeintellect", _request("request-type-corruption"))

    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT request_json FROM remote_eval_outbox").fetchone()[0]
        payload = json.loads(raw)
        payload["timeout_seconds"] = "30"
        corrupted = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        connection.execute(
            "UPDATE remote_eval_outbox SET request_json = ?, request_json_sha256 = ?",
            (corrupted, hashlib.sha256(corrupted.encode()).hexdigest()),
        )

    with pytest.raises(ValueError, match="timeout_seconds must be a finite number"):
        outbox.statuses()


def test_outbox_completed_replay_validates_ledger_checksum_and_projection(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    outbox = ExternalEvalLedgerOutbox(path)
    request = _request("ledger-projection")
    outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, _result(request))

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE remote_eval_outbox SET ledger_json = ledger_json || ' '")
    with pytest.raises(ValueError, match="ledger checksum mismatch"):
        outbox.claim("primeintellect", request)

    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT ledger_json FROM remote_eval_outbox").fetchone()[0].rstrip()
        payload = json.loads(raw)
        payload["detail"] = "different accounting projection"
        corrupted = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        connection.execute(
            "UPDATE remote_eval_outbox SET ledger_json = ?, ledger_sha256 = ?",
            (corrupted, hashlib.sha256(corrupted.encode()).hexdigest()),
        )

    with pytest.raises(ValueError, match="ledger does not match its committed result"):
        outbox.claim("primeintellect", request)


def test_result_model_and_versioned_codec_reject_unknown_status_and_coercible_booleans(tmp_path: Path) -> None:
    request = _request("invalid-status")
    with pytest.raises(ValueError, match="unsupported remote result status"):
        replace(_result(request), status="future_status")  # type: ignore[arg-type]

    path = tmp_path / "invalid-replay.sqlite3"
    outbox = ExternalEvalLedgerOutbox(path)
    request = _request("invalid-boolean")
    outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, _result(request))
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT result_json FROM remote_eval_outbox").fetchone()[0]
        payload = json.loads(raw)
        payload["retryable"] = "false"
        corrupted = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        connection.execute(
            "UPDATE remote_eval_outbox SET result_json = ?, result_sha256 = ?",
            (corrupted, hashlib.sha256(corrupted.encode()).hexdigest()),
        )

    with pytest.raises(ValueError, match="result retryable must be a boolean"):
        outbox.claim("primeintellect", request)

    path = tmp_path / "invalid-ledger.sqlite3"
    outbox = ExternalEvalLedgerOutbox(path)
    request = _request("invalid-ledger-boolean")
    outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, _result(request))
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT ledger_json FROM remote_eval_outbox").fetchone()[0]
        payload = json.loads(raw)
        payload["candidate_succeeded"] = "false"
        corrupted = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        connection.execute(
            "UPDATE remote_eval_outbox SET ledger_json = ?, ledger_sha256 = ?",
            (corrupted, hashlib.sha256(corrupted.encode()).hexdigest()),
        )

    with pytest.raises(ValueError, match="ledger candidate_succeeded must be a boolean"):
        outbox.claim("primeintellect", request)


def test_outbox_commit_rejects_result_subclasses_before_codec_roundtrip(tmp_path: Path) -> None:
    request = _request("result-subclass")
    outbox = ExternalEvalLedgerOutbox(tmp_path / "result-subclass.sqlite3")
    outbox.claim("primeintellect", request)

    def skip_base_validation(_result: object) -> None:
        return None

    unchecked_result_type = type(
        "UncheckedRemoteExecutionResult",
        (RemoteExecutionResult,),
        {"__post_init__": skip_base_validation},
    )
    result = unchecked_result_type(
        task_id=request.task_id,
        provider="primeintellect",
        status="success",
        provenance=remote_request_provenance(request),
    )

    with pytest.raises(TypeError, match="must be a RemoteExecutionResult"):
        outbox.commit("primeintellect", request, result)

    assert outbox.statuses()[0].state == "claimed"


def test_sink_delivery_reservation_serializes_concurrent_workers(tmp_path: Path) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    request = _request("concurrent-delivery")
    claim = outbox.claim("primeintellect", request)
    result = _result(request)
    outbox.commit("primeintellect", request, result)
    barrier = threading.Barrier(2)

    def reserve() -> object:
        barrier.wait(timeout=2)
        try:
            return outbox.reserve_sink_delivery(claim.attempt_id)
        except ExternalEvalSinkDeliveryPendingError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: reserve(), range(2)))

    reservations = [outcome for outcome in outcomes if isinstance(outcome, ExternalEvalSinkDeliveryReservation)]
    pending = [outcome for outcome in outcomes if isinstance(outcome, ExternalEvalSinkDeliveryPendingError)]
    assert len(reservations) == 1
    assert len(pending) == 1
    reservation = reservations[0]
    assert reservation is not None
    assert reservation.attempt_id == claim.attempt_id
    assert reservation.entry == result.to_ledger_entry(attempt_id=claim.attempt_id)


def test_sink_delivery_lease_starts_after_contended_write_lock_is_acquired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    request = _request("contended-delivery-clock")
    claim = outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, _result(request))
    real_connect = outbox._connect
    clock = {"now": 100.0}

    class _ContendedConnection:
        """Advance the clock while BEGIN IMMEDIATE waits for its write lock."""

        def __init__(self) -> None:
            self._connection = real_connect()

        def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
            cursor = self._connection.execute(statement, parameters)
            if statement == "BEGIN IMMEDIATE":
                clock["now"] = 200.0
            return cursor

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

    monkeypatch.setattr(outbox, "_connect", _ContendedConnection)
    monkeypatch.setattr("autocontext.execution.external_eval_outbox.time.time", lambda: clock["now"])

    reservation = outbox.reserve_sink_delivery(claim.attempt_id, lease_seconds=10.0)

    assert reservation is not None
    assert reservation.lease_expires_at == 210.0


def test_sink_delivery_expired_lease_recovers_and_late_failure_cannot_overwrite_success(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    outbox = ExternalEvalLedgerOutbox(path)
    request = _request("delivery-crash")
    claim = outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, _result(request))
    abandoned = outbox.reserve_sink_delivery(claim.attempt_id)
    assert abandoned is not None

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE remote_eval_outbox SET delivery_lease_expires_at = 0 WHERE attempt_id = ?",
            (claim.attempt_id,),
        )
    recovered = outbox.reserve_sink_delivery(claim.attempt_id)
    assert recovered is not None
    assert recovered.attempt_id == abandoned.attempt_id
    assert recovered.lease_token != abandoned.lease_token

    outbox.mark_sink_delivered(recovered)
    outbox.record_sink_failure(abandoned, OSError("late stale failure"))

    status = outbox.statuses()[0]
    assert status.sink_delivered is True
    assert status.delivery_error == ""
    assert outbox.reserve_sink_delivery(claim.attempt_id) is None


def test_sink_delivery_failure_releases_lease_for_retry(tmp_path: Path) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    request = _request("delivery-failure")
    claim = outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, _result(request))
    first = outbox.reserve_sink_delivery(claim.attempt_id)
    assert first is not None

    outbox.record_sink_failure(first, OSError("sink unavailable"))
    assert "sink unavailable" in outbox.statuses()[0].delivery_error

    retry = outbox.reserve_sink_delivery(claim.attempt_id)
    assert retry is not None
    assert retry.lease_token != first.lease_token
    outbox.mark_sink_delivered(retry)
    assert outbox.statuses(unresolved_only=True) == ()


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

    with pytest.raises(RemoteExecutionAccountingError, match="ledger unavailable") as raised:
        failing.execute_request(request, max_retries=3, backoff_seconds=0)

    assert isinstance(raised.value.__cause__, OSError)
    assert _CountingAsyncClient.create_calls == 1
    unresolved = outbox.statuses(unresolved_only=True)
    assert len(unresolved) == 1
    assert unresolved[0].state == "completed"
    assert "ledger unavailable" in unresolved[0].delivery_error

    sinkless_replay = PrimeIntellectClient(api_key="test-key", ledger_outbox=outbox).execute_request(
        request,
        max_retries=3,
        backoff_seconds=0,
    )
    assert sinkless_replay.succeeded is True
    assert _CountingAsyncClient.create_calls == 1
    still_unresolved = outbox.statuses(unresolved_only=True)
    assert len(still_unresolved) == 1
    assert "ledger unavailable" in still_unresolved[0].delivery_error

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
    assert delivered == [replayed.to_ledger_entry(attempt_id=external_eval_attempt_id("primeintellect", request))]
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
    monkeypatch.setattr(outbox, "commit", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")))

    with pytest.raises(RemoteExecutionAccountingError, match="disk unavailable") as raised:
        client.execute_request(request, max_retries=3, backoff_seconds=0)

    assert isinstance(raised.value.__cause__, OSError)
    assert _CountingAsyncClient.create_calls == 1
    with pytest.raises(RemoteExecutionAccountingError, match="reconcile provider accounting") as pending:
        PrimeIntellectClient(api_key="test-key", ledger_outbox=outbox).execute_request(
            request,
            max_retries=3,
            backoff_seconds=0,
        )
    assert isinstance(pending.value.__cause__, ExternalEvalOutboxPendingError)
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

    assert runtime.remote_ledger_outbox.ledger_entries() == (
        result.to_ledger_entry(attempt_id=external_eval_attempt_id("primeintellect", _request("runtime-composition"))),
    )
    assert runtime.remote_ledger_outbox.committed_results() == (result,)
    assert runtime.unresolved_remote_evaluations() == ()


@pytest.mark.parametrize(
    ("offline", "expected_error", "message"),
    [
        (True, OfflineError, "AUTOCONTEXT_OFFLINE"),
        (False, ValueError, "AUTOCONTEXT_PRIMEINTELLECT_API_KEY"),
    ],
)
def test_runtime_without_dispatch_authority_replays_but_cannot_claim_new_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    offline: bool,
    expected_error: type[Exception],
    message: str,
) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    request = _request("restart-without-dispatch-authority")
    committed = _result(request)
    outbox.claim("primeintellect", request)
    outbox.commit("primeintellect", request, committed, sink_required=False)
    settings = AppSettings(
        executor_mode="primeintellect",
        primeintellect_api_key=None,
        offline=offline,
        runs_root=tmp_path / "runs",
    )
    sdk_calls: list[str] = []
    monkeypatch.setattr(
        "autocontext.integrations.primeintellect.client._prime_sandboxes_sdk",
        lambda: sdk_calls.append("sdk") or (_ for _ in ()).throw(AssertionError("SDK gate ran")),
    )

    runtime = build_execution_runtime(settings, remote_ledger_outbox=outbox)

    assert runtime.remote_adapter is not None
    assert runtime.remote_adapter.execute_request(request, max_retries=0) == committed
    assert sdk_calls == []

    fresh = _request("new-work-without-dispatch-authority")
    with pytest.raises(expected_error, match=message):
        runtime.remote_adapter.execute_request(fresh, max_retries=0)

    assert sdk_calls == []
    assert [status.task_id for status in outbox.statuses()] == [request.task_id]


def test_runtime_offline_environment_toggle_still_blocks_new_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outbox = ExternalEvalLedgerOutbox(tmp_path / "ledger.sqlite3")
    runtime = build_execution_runtime(
        AppSettings(
            executor_mode="primeintellect",
            primeintellect_api_key="test-key",
            offline=False,
            runs_root=tmp_path / "runs",
        ),
        remote_ledger_outbox=outbox,
    )
    monkeypatch.setenv("AUTOCONTEXT_OFFLINE", "1")

    assert runtime.remote_adapter is not None
    with pytest.raises(OfflineError, match="AUTOCONTEXT_OFFLINE"):
        runtime.remote_adapter.execute_request(_request("blocked-after-runtime-construction"), max_retries=0)

    assert outbox.statuses() == ()
