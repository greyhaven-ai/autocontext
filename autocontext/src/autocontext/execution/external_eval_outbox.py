"""Durable, idempotent result accounting for paid remote evaluations."""

from __future__ import annotations

import math
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import autocontext.execution._external_eval_outbox_codec as _codec
import autocontext.execution._external_eval_outbox_identity as _identity
import autocontext.execution._external_eval_outbox_store as _store
from autocontext.execution._external_eval_outbox_identity import external_eval_attempt_id
from autocontext.execution.remote_execution import (
    ExternalEvalLedgerEntry,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    remote_request_sha256,
)


class ExternalEvalOutboxConflictError(RuntimeError):
    """Raised when one durable attempt identity is reused for different content."""


class ExternalEvalOutboxPendingError(RuntimeError):
    """Raised when a prior process may already have dispatched the paid request."""


class ExternalEvalSinkDeliveryPendingError(RuntimeError):
    """Raised when another process owns the durable ledger-delivery lease."""


@dataclass(frozen=True, slots=True)
class ExternalEvalOutboxClaim:
    attempt_id: str
    result: RemoteExecutionResult | None = None
    sink_delivered: bool = False


@dataclass(frozen=True, slots=True)
class ExternalEvalSinkDeliveryReservation:
    """Exclusive, expiring permission to deliver one committed ledger entry.

    ``attempt_id`` is the stable idempotency key for the external sink, while
    ``lease_token`` is unique to this delivery worker and is used only for
    compare-and-swap acknowledgement in the SQLite outbox.
    """

    attempt_id: str
    lease_token: str
    lease_expires_at: float
    entry: ExternalEvalLedgerEntry


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
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._instance_id: str = _store.initialize_database(self.path)

    @property
    def instance_id(self) -> str:
        return self._instance_id

    def _connect(self) -> sqlite3.Connection:
        return _store.connect_database(self.path, expected_instance_id=self._instance_id)

    def replay(self, provider: str, request: RemoteExecutionRequest) -> ExternalEvalOutboxClaim | None:
        """Return a committed result without creating a new durable claim.

        A missing row returns ``None`` so mutable dispatch-only preflight can
        run before ``claim`` atomically inserts the paid-work identity. An
        existing unresolved claim still fails closed.
        """

        attempt_id = external_eval_attempt_id(provider, request)
        request_digest = remote_request_sha256(request)
        request_json = _codec.canonical_json(_codec.request_payload(provider, request))
        connection = self._connect()
        try:
            resolved = self._resolve_existing_claim_row(
                connection,
                provider=provider,
                request=request,
                attempt_id=attempt_id,
                request_sha256=request_digest,
                request_json=request_json,
            )
            if resolved is None:
                return None
            row, attempt_id, request_digest = resolved
            return self._claim_from_existing_row(
                row,
                provider=provider,
                request=request,
                attempt_id=attempt_id,
                request_sha256=request_digest,
            )
        finally:
            connection.close()

    def claim(self, provider: str, request: RemoteExecutionRequest) -> ExternalEvalOutboxClaim:
        attempt_id = external_eval_attempt_id(provider, request)
        request_digest = remote_request_sha256(request)
        request_json = _codec.canonical_json(_codec.request_payload(provider, request))
        request_json_digest = _codec.sha256(request_json)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            resolved = self._resolve_existing_claim_row(
                connection,
                provider=provider,
                request=request,
                attempt_id=attempt_id,
                request_sha256=request_digest,
                request_json=request_json,
            )
            if resolved is None:
                connection.execute(
                    """
                    INSERT INTO remote_eval_outbox (
                        attempt_id, provider, task_id, request_sha256,
                        request_json, request_json_sha256, state, sink_delivered, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'claimed', 0, ?)
                    """,
                    (
                        attempt_id,
                        provider,
                        request.task_id,
                        request_digest,
                        request_json,
                        request_json_digest,
                        time.time(),
                    ),
                )
                connection.commit()
                return ExternalEvalOutboxClaim(attempt_id=attempt_id)
            row, attempt_id, request_digest = resolved
            claim = self._claim_from_existing_row(
                row,
                provider=provider,
                request=request,
                attempt_id=attempt_id,
                request_sha256=request_digest,
            )
            connection.commit()
            return claim
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
        *,
        sink_required: bool = True,
    ) -> str:
        """Durably commit one result before it is returned to the caller.

        A caller with no external sink settles that absence atomically by
        passing ``sink_required=False``. A later sinkless replay must not infer
        that an existing undelivered row had no delivery obligation.
        """

        if not isinstance(sink_required, bool):
            raise TypeError("external-evaluation sink_required must be boolean")
        if type(result) is not RemoteExecutionResult:
            raise TypeError("external-evaluation result must be a RemoteExecutionResult")

        attempt_id = external_eval_attempt_id(provider, request)
        request_digest = remote_request_sha256(request)
        request_json = _codec.canonical_json(_codec.request_payload(provider, request))
        result_json = _codec.canonical_json(_codec.result_payload(result))
        decoded_result = _codec.result_from_payload(_codec.load_json(result_json, label="external-evaluation result"))
        if decoded_result != result:
            raise ValueError("external-evaluation result does not round-trip through the versioned codec")
        result_digest = _codec.sha256(result_json)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            resolved = self._resolve_existing_claim_row(
                connection,
                provider=provider,
                request=request,
                attempt_id=attempt_id,
                request_sha256=request_digest,
                request_json=request_json,
            )
            if resolved is None:
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} completed without a durable pre-dispatch claim"
                )
            row, attempt_id, request_digest = resolved
            self._validate_result(provider, request, decoded_result, request_sha256=request_digest)
            ledger_entry = result.to_ledger_entry(attempt_id=attempt_id)
            ledger_json = _codec.canonical_json(_codec.ledger_payload(ledger_entry))
            if _codec.ledger_from_payload(_codec.load_json(ledger_json, label="external-evaluation ledger")) != ledger_entry:
                raise ValueError("external-evaluation ledger does not round-trip through the versioned codec")
            ledger_digest = _codec.sha256(ledger_json)
            state = _codec.expect_str(row["state"], "outbox state")
            if state == "completed":
                existing_result = _codec.checked_payload(
                    row["result_json"],
                    row["result_sha256"],
                    label=f"remote evaluation {attempt_id} result",
                )
                existing_ledger = _codec.checked_payload(
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
            if state != "claimed":
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} has unsupported outbox state {row['state']!r}"
                )
            connection.execute(
                """
                UPDATE remote_eval_outbox
                   SET state = 'completed', result_json = ?, result_sha256 = ?,
                       ledger_json = ?, ledger_sha256 = ?, sink_delivered = ?,
                       completed_at = ?
                 WHERE attempt_id = ? AND state = 'claimed'
                """,
                (
                    result_json,
                    result_digest,
                    ledger_json,
                    ledger_digest,
                    int(not sink_required),
                    time.time(),
                    attempt_id,
                ),
            )
            connection.commit()
            return attempt_id
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_sink_delivery(
        self,
        attempt_id: str,
        *,
        lease_seconds: float = 300.0,
    ) -> ExternalEvalSinkDeliveryReservation | None:
        """Reserve exclusive delivery of a committed ledger entry.

        ``None`` means the entry was already acknowledged. An unexpired lease
        fails closed; after expiry another worker may reserve the same stable
        attempt ID and rely on sink-side idempotency to recover a crash between
        the external side effect and its local acknowledgement.
        """

        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)):
            raise TypeError("ledger-delivery lease_seconds must be a number")
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("ledger-delivery lease_seconds must be positive and finite")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Start the lease only after SQLite grants the write lock.  A
            # contended BEGIN may wait for the busy timeout; measuring before
            # it can return a reservation that is already expired.
            now = time.time()
            lease_expires_at = now + float(lease_seconds)
            lease_token = secrets.token_urlsafe(32)
            row = connection.execute(
                """
                SELECT state, sink_delivered, delivery_lease_token,
                       delivery_lease_expires_at, result_json, result_sha256,
                       ledger_json, ledger_sha256
                  FROM remote_eval_outbox
                 WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None or _codec.expect_str(row["state"], "outbox state") != "completed":
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} cannot reserve ledger delivery before result commit"
                )
            if _codec.sqlite_bool(row["sink_delivered"], label="outbox sink_delivered"):
                connection.commit()
                return None
            current_token = row["delivery_lease_token"]
            current_expiry = row["delivery_lease_expires_at"]
            if (current_token is None) != (current_expiry is None):
                raise ValueError(f"remote evaluation {attempt_id} has an invalid ledger-delivery lease")
            if current_token is not None:
                _codec.expect_str(current_token, "ledger-delivery lease token", nonempty=True)
                expiry = _codec.expect_number(current_expiry, "ledger-delivery lease expiry")
                if expiry > now:
                    raise ExternalEvalSinkDeliveryPendingError(
                        f"remote evaluation {attempt_id} ledger delivery is leased by another process"
                    )
            ledger_json = _codec.checked_payload(
                row["ledger_json"],
                row["ledger_sha256"],
                label=f"remote evaluation {attempt_id} ledger",
            )
            entry = _codec.ledger_from_payload(_codec.load_json(ledger_json, label=f"remote evaluation {attempt_id} ledger"))
            result_json = _codec.checked_payload(
                row["result_json"],
                row["result_sha256"],
                label=f"remote evaluation {attempt_id} result",
            )
            result = _codec.result_from_payload(_codec.load_json(result_json, label=f"remote evaluation {attempt_id} result"))
            if entry != result.to_ledger_entry(attempt_id=attempt_id):
                raise ValueError(f"remote evaluation {attempt_id} ledger does not match its committed result")
            cursor = connection.execute(
                """
                UPDATE remote_eval_outbox
                   SET delivery_lease_token = ?, delivery_lease_expires_at = ?
                 WHERE attempt_id = ? AND state = 'completed' AND sink_delivered = 0
                   AND (
                       delivery_lease_token IS NULL
                       OR delivery_lease_expires_at <= ?
                   )
                """,
                (lease_token, lease_expires_at, attempt_id, now),
            )
            if cursor.rowcount != 1:
                raise ExternalEvalSinkDeliveryPendingError(
                    f"remote evaluation {attempt_id} ledger delivery could not be reserved"
                )
            connection.commit()
            return ExternalEvalSinkDeliveryReservation(
                attempt_id=attempt_id,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
                entry=entry,
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def mark_sink_delivered(self, reservation: ExternalEvalSinkDeliveryReservation) -> None:
        """Acknowledge delivery if ``reservation`` still owns the lease."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE remote_eval_outbox
                   SET sink_delivered = 1, delivery_error = '',
                       delivery_lease_token = NULL, delivery_lease_expires_at = NULL
                 WHERE attempt_id = ? AND state = 'completed' AND sink_delivered = 0
                   AND delivery_lease_token = ?
                """,
                (reservation.attempt_id, reservation.lease_token),
            )
            if cursor.rowcount != 1:
                if _store.delivery_was_already_acknowledged(connection, reservation.attempt_id):
                    connection.commit()
                    return
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {reservation.attempt_id} ledger-delivery lease was lost before acknowledgement"
                )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def record_sink_failure(
        self,
        reservation: ExternalEvalSinkDeliveryReservation,
        error: BaseException,
    ) -> None:
        """Release a failed delivery lease without overwriting later success."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE remote_eval_outbox
                   SET delivery_error = ?, delivery_lease_token = NULL,
                       delivery_lease_expires_at = NULL
                 WHERE attempt_id = ? AND state = 'completed' AND sink_delivered = 0
                   AND delivery_lease_token = ?
                """,
                (
                    f"{type(error).__name__}: {error}",
                    reservation.attempt_id,
                    reservation.lease_token,
                ),
            )
            if cursor.rowcount != 1:
                if _store.delivery_was_already_acknowledged(connection, reservation.attempt_id):
                    connection.commit()
                    return
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {reservation.attempt_id} ledger-delivery lease was lost before failure recording"
                )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def statuses(self, *, unresolved_only: bool = False) -> tuple[ExternalEvalOutboxStatus, ...]:
        where = "WHERE state != 'completed' OR sink_delivered = 0" if unresolved_only else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT attempt_id, provider, task_id, request_sha256, request_json,
                       request_json_sha256, state,
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
            attempt_id = _codec.expect_str(row["attempt_id"], "outbox attempt_id", nonempty=True)
            request_json = _codec.checked_payload(
                row["request_json"],
                row["request_json_sha256"],
                label=f"remote evaluation {attempt_id} request",
            )
            request_payload = _codec.request_status_payload(
                _codec.load_json(request_json, label=f"remote evaluation {attempt_id} request")
            )
            provider = _codec.expect_str(row["provider"], "outbox provider", nonempty=True)
            task_id = _codec.expect_str(row["task_id"], "outbox task_id", nonempty=True)
            request_digest = _codec.expect_sha256(row["request_sha256"], "outbox request_sha256")
            if (
                request_payload["provider"] != provider
                or request_payload["task_id"] != task_id
                or request_payload["request_sha256"] != request_digest
            ):
                raise ValueError(f"remote evaluation {attempt_id} request identity mismatch")
            state = _codec.expect_str(row["state"], "outbox state")
            if state not in {"claimed", "completed"}:
                raise ValueError(f"remote evaluation {attempt_id} has unsupported outbox state {state!r}")
            completed_at = row["completed_at"]
            statuses.append(
                ExternalEvalOutboxStatus(
                    attempt_id=attempt_id,
                    provider=provider,
                    task_id=task_id,
                    request_sha256=request_digest,
                    state=state,
                    sink_delivered=_codec.sqlite_bool(row["sink_delivered"], label="outbox sink_delivered"),
                    created_at=_codec.expect_number(row["created_at"], "outbox created_at"),
                    completed_at=(
                        _codec.expect_number(completed_at, "outbox completed_at")
                        if completed_at is not None
                        else None
                    ),
                    delivery_error=_codec.expect_str(row["delivery_error"], "outbox delivery_error"),
                    timeout_seconds=request_payload["timeout_seconds"],
                    requested_cpu_cores=request_payload["cpu_cores"],
                    requested_memory_gb=request_payload["memory_gb"],
                    requested_disk_gb=request_payload["disk_gb"],
                    requested_accelerator_kind=request_payload["accelerator_kind"],
                    requested_accelerator_count=request_payload["accelerator_count"],
                )
            )
        return tuple(statuses)

    def ledger_entries(self) -> tuple[ExternalEvalLedgerEntry, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT attempt_id, result_json, result_sha256, ledger_json, ledger_sha256
                  FROM remote_eval_outbox
                 WHERE state = 'completed'
                 ORDER BY completed_at, attempt_id
                """
            ).fetchall()
        finally:
            connection.close()
        entries = []
        for row in rows:
            attempt_id = _codec.expect_str(row["attempt_id"], "outbox attempt_id", nonempty=True)
            raw = _codec.checked_payload(
                row["ledger_json"],
                row["ledger_sha256"],
                label=f"remote evaluation {attempt_id} ledger",
            )
            entry = _codec.ledger_from_payload(_codec.load_json(raw, label=f"remote evaluation {attempt_id} ledger"))
            result_json = _codec.checked_payload(
                row["result_json"],
                row["result_sha256"],
                label=f"remote evaluation {attempt_id} result",
            )
            result = _codec.result_from_payload(_codec.load_json(result_json, label=f"remote evaluation {attempt_id} result"))
            if entry != result.to_ledger_entry(attempt_id=attempt_id):
                raise ValueError(f"remote evaluation {attempt_id} ledger does not match its committed result")
            entries.append(entry)
        return tuple(entries)

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
            _codec.result_from_payload(
                _codec.load_json(
                    _codec.checked_payload(
                        row["result_json"],
                        row["result_sha256"],
                        label=f"remote evaluation {row['attempt_id']} result",
                    ),
                    label=f"remote evaluation {row['attempt_id']} result",
                )
            )
            for row in rows
        )

    def _resolve_existing_claim_row(
        self,
        connection: sqlite3.Connection,
        *,
        provider: str,
        request: RemoteExecutionRequest,
        attempt_id: str,
        request_sha256: str,
        request_json: str,
    ) -> tuple[sqlite3.Row, str, str] | None:
        """Resolve one exact or prerelease numeric identity, failing on aliases."""

        exact = _store.select_claim_row(connection, attempt_id)
        if exact is not None:
            self._validate_identity(
                exact,
                attempt_id=attempt_id,
                provider=provider,
                task_id=request.task_id,
                request_sha256=request_sha256,
                request_json=request_json,
            )
        legacy = self._select_legacy_numeric_claim_row(
            connection,
            provider=provider,
            request=request,
            exclude_attempt_id=attempt_id,
        )
        if exact is not None and legacy is not None:
            raise ExternalEvalOutboxConflictError(
                f"multiple numeric identities match remote evaluation task {request.task_id!r}"
            )
        if exact is not None:
            return exact, attempt_id, request_sha256
        return legacy

    @staticmethod
    def _select_legacy_numeric_claim_row(
        connection: sqlite3.Connection,
        *,
        provider: str,
        request: RemoteExecutionRequest,
        exclude_attempt_id: str,
    ) -> tuple[sqlite3.Row, str, str] | None:
        """Find an exact prerelease identity that differs only by JSON number spelling."""

        rows = connection.execute(
            """
            SELECT attempt_id, provider, task_id, request_sha256, request_json,
                   request_json_sha256, state, result_json, result_sha256,
                   ledger_json, ledger_sha256, sink_delivered
              FROM remote_eval_outbox
             WHERE provider = ? AND task_id = ?
            """,
            (provider, request.task_id),
        ).fetchall()
        matches: list[tuple[sqlite3.Row, str, str]] = []
        for row in rows:
            attempt_id = _codec.expect_sha256(row["attempt_id"], "outbox attempt_id")
            if attempt_id == exclude_attempt_id:
                continue
            stored_request_json = _codec.checked_payload(
                row["request_json"],
                row["request_json_sha256"],
                label=f"remote evaluation {attempt_id} request",
            )
            raw_payload = _codec.load_json(stored_request_json, label=f"remote evaluation {attempt_id} request")
            status_payload = _codec.request_status_payload(raw_payload)
            request_digest = _codec.expect_sha256(row["request_sha256"], "outbox request_sha256")
            if (
                _codec.expect_str(row["provider"], "outbox provider", nonempty=True) != provider
                or _codec.expect_str(row["task_id"], "outbox task_id", nonempty=True) != request.task_id
                or status_payload["provider"] != provider
                or status_payload["task_id"] != request.task_id
                or status_payload["request_sha256"] != request_digest
            ):
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} has conflicting legacy request identity"
                )
            expected_attempt_id = _identity.attempt_id_from_digest(provider, request.task_id, request_digest)
            if attempt_id != expected_attempt_id:
                raise ExternalEvalOutboxConflictError(
                    f"remote evaluation {attempt_id} has a conflicting legacy attempt identity"
                )
            try:
                candidates = _identity.legacy_numeric_request_sha256_candidates(request, raw_payload)
            except _identity.LegacyNumericIdentityConflictError as exc:
                raise ExternalEvalOutboxConflictError(str(exc)) from exc
            if request_digest not in candidates:
                if request.strict_task_identity:
                    raise ExternalEvalOutboxConflictError(
                        f"remote evaluation task {request.task_id!r} is already bound to a different durable request identity"
                    )
                continue
            matches.append((row, attempt_id, request_digest))
        if len(matches) > 1:
            raise ExternalEvalOutboxConflictError(
                f"multiple legacy numeric identities match remote evaluation task {request.task_id!r}"
            )
        return matches[0] if matches else None

    def _claim_from_existing_row(
        self,
        row: sqlite3.Row,
        *,
        provider: str,
        request: RemoteExecutionRequest,
        attempt_id: str,
        request_sha256: str,
    ) -> ExternalEvalOutboxClaim:
        state = _codec.expect_str(row["state"], "outbox state")
        if state == "claimed":
            raise ExternalEvalOutboxPendingError(
                f"remote evaluation {attempt_id} has an unresolved durable claim; reconcile provider accounting before retrying"
            )
        if state != "completed":
            raise ExternalEvalOutboxConflictError(f"remote evaluation {attempt_id} has unsupported outbox state {state!r}")
        result_json = _codec.checked_payload(
            row["result_json"],
            row["result_sha256"],
            label=f"remote evaluation {attempt_id} result",
        )
        result = _codec.result_from_payload(_codec.load_json(result_json, label=f"remote evaluation {attempt_id} result"))
        self._validate_result(provider, request, result, request_sha256=request_sha256)
        ledger_json = _codec.checked_payload(
            row["ledger_json"],
            row["ledger_sha256"],
            label=f"remote evaluation {attempt_id} ledger",
        )
        ledger = _codec.ledger_from_payload(_codec.load_json(ledger_json, label=f"remote evaluation {attempt_id} ledger"))
        if ledger != result.to_ledger_entry(attempt_id=attempt_id):
            raise ValueError(f"remote evaluation {attempt_id} ledger does not match its committed result")
        return ExternalEvalOutboxClaim(
            attempt_id=attempt_id,
            result=result,
            sink_delivered=_codec.sqlite_bool(row["sink_delivered"], label="outbox sink_delivered"),
        )

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
        if not _store.claim_identity_matches(
            row,
            attempt_id=attempt_id,
            provider=provider,
            task_id=task_id,
            request_sha256=request_sha256,
            request_json=request_json,
        ):
            raise ExternalEvalOutboxConflictError(f"remote evaluation {attempt_id} was reused with conflicting request identity")

    @staticmethod
    def _validate_result(
        provider: str,
        request: RemoteExecutionRequest,
        result: RemoteExecutionResult,
        *,
        request_sha256: str | None = None,
    ) -> None:
        if not _identity.result_matches_request(provider, request, result, request_sha256=request_sha256):
            raise ExternalEvalOutboxConflictError("remote result provenance does not match its durable request claim")




__all__ = [
    "ExternalEvalLedgerOutbox",
    "ExternalEvalOutboxClaim",
    "ExternalEvalOutboxConflictError",
    "ExternalEvalOutboxPendingError",
    "ExternalEvalSinkDeliveryPendingError",
    "ExternalEvalSinkDeliveryReservation",
    "ExternalEvalOutboxStatus",
    "external_eval_attempt_id",
]
