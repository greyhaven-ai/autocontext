"""Durable, idempotent result accounting for paid remote evaluations."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from autocontext.execution.remote_execution import (
    ExternalEvalLedgerEntry,
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionProvenance,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteExecutionStatus,
    RemoteInputProvenance,
    RemoteOutputArtifact,
    RemoteResolvedEnvironment,
    RemoteResourceUsage,
    remote_request_provenance,
    remote_request_sha256,
)

_SCHEMA_VERSION = 1


class ExternalEvalOutboxConflictError(RuntimeError):
    """Raised when one durable attempt identity is reused for different content."""


class ExternalEvalOutboxPendingError(RuntimeError):
    """Raised when a prior process may already have dispatched the paid request."""


@dataclass(frozen=True, slots=True)
class ExternalEvalOutboxClaim:
    attempt_id: str
    result: RemoteExecutionResult | None = None
    sink_delivered: bool = False


@dataclass(frozen=True, slots=True)
class ExternalEvalOutboxStatus:
    attempt_id: str
    provider: str
    task_id: str
    request_sha256: str
    state: str
    sink_delivered: bool
    created_at: float
    completed_at: float | None
    delivery_error: str
    timeout_seconds: float
    requested_cpu_cores: float
    requested_memory_gb: float
    requested_disk_gb: float
    requested_accelerator_kind: str
    requested_accelerator_count: int


class ExternalEvalLedgerOutbox:
    """SQLite result journal that prevents ambiguous paid-task replay.

    ``claim`` commits an in-flight identity before provider dispatch. A terminal
    result and its ledger projection are then committed in one transaction
    before the caller can observe completion. Reopening the same database
    returns an already-committed result, while an abandoned in-flight claim
    fails closed for operator reconciliation instead of dispatching again.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def claim(self, provider: str, request: RemoteExecutionRequest) -> ExternalEvalOutboxClaim:
        attempt_id = external_eval_attempt_id(provider, request)
        request_digest = remote_request_sha256(request)
        request_json = _canonical_json(_request_payload(provider, request))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT provider, task_id, request_sha256, request_json, state,
                       result_json, result_sha256, sink_delivered
                  FROM remote_eval_outbox
                 WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO remote_eval_outbox (
                        attempt_id, provider, task_id, request_sha256,
                        request_json, state, sink_delivered, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'claimed', 0, ?)
                    """,
                    (attempt_id, provider, request.task_id, request_digest, request_json, time.time()),
                )
                connection.commit()
                return ExternalEvalOutboxClaim(attempt_id=attempt_id)
            self._validate_identity(
                row,
                attempt_id=attempt_id,
                provider=provider,
                task_id=request.task_id,
                request_sha256=request_digest,
                request_json=request_json,
            )
            if str(row["state"]) == "claimed":
                connection.commit()
                raise ExternalEvalOutboxPendingError(
                    f"remote evaluation {attempt_id} has an unresolved durable claim; "
                    "reconcile provider accounting before retrying"
                )
            if str(row["state"]) != "completed":
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} has unsupported outbox state {row['state']!r}"
                )
            result_json = _checked_payload(
                row["result_json"],
                row["result_sha256"],
                label=f"remote evaluation {attempt_id} result",
            )
            result = _result_from_payload(json.loads(result_json))
            self._validate_result(provider, request, result)
            connection.commit()
            return ExternalEvalOutboxClaim(
                attempt_id=attempt_id,
                result=result,
                sink_delivered=bool(row["sink_delivered"]),
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def commit(
        self,
        provider: str,
        request: RemoteExecutionRequest,
        result: RemoteExecutionResult,
    ) -> str:
        """Durably commit one result before it is returned to the caller."""

        self._validate_result(provider, request, result)
        attempt_id = external_eval_attempt_id(provider, request)
        result_json = _canonical_json(_result_payload(result))
        result_digest = _sha256(result_json)
        ledger_json = _canonical_json(_ledger_payload(result.to_ledger_entry()))
        ledger_digest = _sha256(ledger_json)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT provider, task_id, request_sha256, request_json, state,
                       result_json, result_sha256, ledger_json, ledger_sha256
                  FROM remote_eval_outbox
                 WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} completed without a durable pre-dispatch claim"
                )
            self._validate_identity(
                row,
                attempt_id=attempt_id,
                provider=provider,
                task_id=request.task_id,
                request_sha256=remote_request_sha256(request),
                request_json=_canonical_json(_request_payload(provider, request)),
            )
            if str(row["state"]) == "completed":
                existing_result = _checked_payload(
                    row["result_json"],
                    row["result_sha256"],
                    label=f"remote evaluation {attempt_id} result",
                )
                existing_ledger = _checked_payload(
                    row["ledger_json"],
                    row["ledger_sha256"],
                    label=f"remote evaluation {attempt_id} ledger",
                )
                if existing_result != result_json or existing_ledger != ledger_json:
                    raise ExternalEvalOutboxConflictError(
                        f"remote evaluation {attempt_id} already has different committed content"
                    )
                connection.commit()
                return attempt_id
            if str(row["state"]) != "claimed":
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} has unsupported outbox state {row['state']!r}"
                )
            connection.execute(
                """
                UPDATE remote_eval_outbox
                   SET state = 'completed', result_json = ?, result_sha256 = ?,
                       ledger_json = ?, ledger_sha256 = ?, completed_at = ?
                 WHERE attempt_id = ? AND state = 'claimed'
                """,
                (result_json, result_digest, ledger_json, ledger_digest, time.time(), attempt_id),
            )
            connection.commit()
            return attempt_id
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def mark_sink_delivered(self, attempt_id: str) -> None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE remote_eval_outbox
                   SET sink_delivered = 1, delivery_error = ''
                 WHERE attempt_id = ? AND state = 'completed'
                """,
                (attempt_id,),
            )
            if cursor.rowcount != 1:
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} cannot acknowledge ledger delivery before result commit"
                )
            connection.commit()
        finally:
            connection.close()

    def record_sink_failure(self, attempt_id: str, error: BaseException) -> None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE remote_eval_outbox
                   SET delivery_error = ?
                 WHERE attempt_id = ? AND state = 'completed'
                """,
                (f"{type(error).__name__}: {error}", attempt_id),
            )
            if cursor.rowcount != 1:
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} cannot record ledger delivery failure before result commit"
                )
            connection.commit()
        finally:
            connection.close()

    def statuses(self, *, unresolved_only: bool = False) -> tuple[ExternalEvalOutboxStatus, ...]:
        where = "WHERE state != 'completed' OR sink_delivered = 0" if unresolved_only else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT attempt_id, provider, task_id, request_sha256, request_json, state,
                       sink_delivered, created_at, completed_at, delivery_error
                  FROM remote_eval_outbox
                  {where}
                 ORDER BY created_at, attempt_id
                """
            ).fetchall()
        finally:
            connection.close()
        statuses = []
        for row in rows:
            request_payload = json.loads(str(row["request_json"]))
            resources = dict(request_payload["resources"])
            accelerator = resources.get("accelerator")
            accelerator_payload = dict(accelerator) if isinstance(accelerator, dict) else {}
            statuses.append(
                ExternalEvalOutboxStatus(
                    attempt_id=str(row["attempt_id"]),
                    provider=str(row["provider"]),
                    task_id=str(row["task_id"]),
                    request_sha256=str(row["request_sha256"]),
                    state=str(row["state"]),
                    sink_delivered=bool(row["sink_delivered"]),
                    created_at=float(row["created_at"]),
                    completed_at=float(row["completed_at"]) if row["completed_at"] is not None else None,
                    delivery_error=str(row["delivery_error"]),
                    timeout_seconds=float(request_payload["timeout_seconds"]),
                    requested_cpu_cores=float(resources["cpu_cores"]),
                    requested_memory_gb=float(resources["memory_gb"]),
                    requested_disk_gb=float(resources["disk_gb"]),
                    requested_accelerator_kind=str(accelerator_payload.get("kind", "")),
                    requested_accelerator_count=int(accelerator_payload.get("count", 0)),
                )
            )
        return tuple(statuses)

    def ledger_entries(self) -> tuple[ExternalEvalLedgerEntry, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT attempt_id, ledger_json, ledger_sha256
                  FROM remote_eval_outbox
                 WHERE state = 'completed'
                 ORDER BY completed_at, attempt_id
                """
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            _ledger_from_payload(
                json.loads(
                    _checked_payload(
                        row["ledger_json"],
                        row["ledger_sha256"],
                        label=f"remote evaluation {row['attempt_id']} ledger",
                    )
                )
            )
            for row in rows
        )

    def committed_results(self) -> tuple[RemoteExecutionResult, ...]:
        """Return checksum-verified results, including retry and cleanup lineage."""

        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT attempt_id, result_json, result_sha256
                  FROM remote_eval_outbox
                 WHERE state = 'completed'
                 ORDER BY completed_at, attempt_id
                """
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            _result_from_payload(
                json.loads(
                    _checked_payload(
                        row["result_json"],
                        row["result_sha256"],
                        label=f"remote evaluation {row['attempt_id']} result",
                    )
                )
            )
            for row in rows
        )

    def _initialize(self) -> None:
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"external-evaluation outbox schema {version} is newer than supported schema {_SCHEMA_VERSION}"
                )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS remote_eval_outbox (
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
                    completed_at REAL,
                    CHECK (
                        (state = 'claimed' AND result_json IS NULL AND result_sha256 IS NULL
                         AND ledger_json IS NULL AND ledger_sha256 IS NULL AND completed_at IS NULL)
                        OR
                        (state = 'completed' AND result_json IS NOT NULL AND result_sha256 IS NOT NULL
                         AND ledger_json IS NOT NULL AND ledger_sha256 IS NOT NULL AND completed_at IS NOT NULL)
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS remote_eval_outbox_request
                    ON remote_eval_outbox(provider, task_id, request_sha256)
                """
            )
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            connection.commit()
        finally:
            connection.close()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @staticmethod
    def _validate_identity(
        row: sqlite3.Row,
        *,
        attempt_id: str,
        provider: str,
        task_id: str,
        request_sha256: str,
        request_json: str,
    ) -> None:
        actual = (str(row["provider"]), str(row["task_id"]), str(row["request_sha256"]), str(row["request_json"]))
        expected = (provider, task_id, request_sha256, request_json)
        if actual != expected:
            raise ExternalEvalOutboxConflictError(f"remote evaluation {attempt_id} was reused with conflicting request identity")

    @staticmethod
    def _validate_result(
        provider: str,
        request: RemoteExecutionRequest,
        result: RemoteExecutionResult,
    ) -> None:
        if result.provider != provider or result.task_id != request.task_id:
            raise ExternalEvalOutboxConflictError("remote result provider/task identity does not match its durable claim")
        expected_request_digest = remote_request_sha256(request)
        if result.provenance.request_sha256 != expected_request_digest:
            raise ExternalEvalOutboxConflictError("remote result provenance does not match its durable request claim")


def external_eval_attempt_id(provider: str, request: RemoteExecutionRequest) -> str:
    if not provider.strip():
        raise ValueError("external evaluation provider must be non-empty")
    return _sha256(
        _canonical_json(
            {
                "provider": provider,
                "task_id": request.task_id,
                "request_sha256": remote_request_sha256(request),
            }
        )
    )


def _request_payload(provider: str, request: RemoteExecutionRequest) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": provider,
        "task_id": request.task_id,
        "request_sha256": remote_request_sha256(request),
        "provenance": asdict(remote_request_provenance(request)),
        "lifecycle": request.lifecycle,
        "timeout_seconds": request.timeout_seconds,
        "resources": asdict(request.resources),
        "region": request.region,
        "required_telemetry": sorted(request.required_telemetry),
        "network_policy": request.network_policy,
        "expected_outputs": list(request.expected_outputs),
        "metadata": dict(request.metadata),
    }


def _result_payload(result: RemoteExecutionResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": result.task_id,
        "provider": result.provider,
        "status": result.status,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "artifacts": [
            {
                "name": artifact.name,
                "content_base64": base64.b64encode(artifact.content).decode("ascii"),
                "media_type": artifact.media_type,
            }
            for artifact in result.artifacts
        ],
        "events": [asdict(event) for event in result.events],
        "usage": asdict(result.usage),
        "cleanup": asdict(result.cleanup),
        "error": result.error,
        "session_id": result.session_id,
        "provenance": asdict(result.provenance),
        "retryable": result.retryable,
    }


def _result_from_payload(payload: Any) -> RemoteExecutionResult:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported external-evaluation result payload")
    try:
        artifacts = tuple(
            RemoteOutputArtifact(
                name=str(item["name"]),
                content=base64.b64decode(str(item["content_base64"]), validate=True),
                media_type=str(item["media_type"]),
            )
            for item in payload["artifacts"]
        )
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError("invalid external-evaluation result artifacts") from exc
    return RemoteExecutionResult(
        task_id=str(payload["task_id"]),
        provider=str(payload["provider"]),
        status=cast(RemoteExecutionStatus, str(payload["status"])),
        stdout=str(payload["stdout"]),
        stderr=str(payload["stderr"]),
        exit_code=int(payload["exit_code"]) if payload["exit_code"] is not None else None,
        artifacts=artifacts,
        events=tuple(
            RemoteExecutionEvent(
                sequence=int(event["sequence"]),
                event_type=str(event["event_type"]),
                message=str(event["message"]),
                fields=dict(event["fields"]),
            )
            for event in payload["events"]
        ),
        usage=RemoteResourceUsage(**dict(payload["usage"])),
        cleanup=RemoteCleanupOutcome(**dict(payload["cleanup"])),
        error=str(payload["error"]),
        session_id=str(payload["session_id"]),
        provenance=_provenance_from_payload(payload["provenance"]),
        retryable=bool(payload["retryable"]),
    )


def _ledger_payload(entry: ExternalEvalLedgerEntry) -> dict[str, Any]:
    return {"schema_version": 1, **asdict(entry)}


def _ledger_from_payload(payload: Any) -> ExternalEvalLedgerEntry:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported external-evaluation ledger payload")
    return ExternalEvalLedgerEntry(
        task_id=str(payload["task_id"]),
        provider=str(payload["provider"]),
        status=cast(RemoteExecutionStatus, str(payload["status"])),
        candidate_succeeded=bool(payload["candidate_succeeded"]),
        infrastructure_succeeded=bool(payload["infrastructure_succeeded"]),
        exit_code=int(payload["exit_code"]) if payload["exit_code"] is not None else None,
        usage=RemoteResourceUsage(**dict(payload["usage"])),
        cleanup=RemoteCleanupOutcome(**dict(payload["cleanup"])),
        detail=str(payload["detail"]),
        provenance=_provenance_from_payload(payload["provenance"]),
        retryable=bool(payload["retryable"]),
    )


def _provenance_from_payload(payload: Any) -> RemoteExecutionProvenance:
    if not isinstance(payload, dict):
        raise ValueError("invalid external-evaluation provenance payload")
    values = dict(payload)
    values["inputs"] = tuple(RemoteInputProvenance(**dict(item)) for item in values.get("inputs", ()))
    values["resolved"] = RemoteResolvedEnvironment(**dict(values.get("resolved", {})))
    values["required_telemetry"] = tuple(str(item) for item in values.get("required_telemetry", ()))
    return RemoteExecutionProvenance(**values)


def _checked_payload(raw: Any, expected_sha256: Any, *, label: str) -> str:
    if not isinstance(raw, str) or not isinstance(expected_sha256, str) or _sha256(raw) != expected_sha256:
        raise ValueError(f"{label} checksum mismatch")
    return raw


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "ExternalEvalLedgerOutbox",
    "ExternalEvalOutboxClaim",
    "ExternalEvalOutboxConflictError",
    "ExternalEvalOutboxPendingError",
    "ExternalEvalOutboxStatus",
    "external_eval_attempt_id",
]
