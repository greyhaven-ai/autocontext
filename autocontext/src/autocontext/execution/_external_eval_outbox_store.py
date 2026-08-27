"""SQLite schema and low-level row access for the external-evaluation outbox."""

from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path
from typing import cast

from autocontext.execution._external_eval_outbox_codec import (
    canonical_json,
    checked_payload,
    expect_sha256,
    expect_str,
    load_json,
    request_status_payload,
    sha256,
    sqlite_bool,
)

SCHEMA_VERSION = 3


def initialize_database(path: Path) -> str:
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        # Read the migration base only after acquiring the write lock. Another
        # process may have upgraded the database while this initializer waited;
        # using a version sampled before BEGIN could then downgrade its schema.
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"external-evaluation outbox schema {version} is newer than supported schema {SCHEMA_VERSION}"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS remote_eval_outbox (
                attempt_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                task_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL,
                request_json_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('claimed', 'completed')),
                result_json TEXT,
                result_sha256 TEXT,
                ledger_json TEXT,
                ledger_sha256 TEXT,
                sink_delivered INTEGER NOT NULL DEFAULT 0 CHECK (sink_delivered IN (0, 1)),
                delivery_error TEXT NOT NULL DEFAULT '',
                delivery_lease_token TEXT,
                delivery_lease_expires_at REAL,
                created_at REAL NOT NULL,
                completed_at REAL,
                CHECK (
                    (state = 'claimed' AND result_json IS NULL AND result_sha256 IS NULL
                     AND ledger_json IS NULL AND ledger_sha256 IS NULL AND completed_at IS NULL)
                    OR
                    (state = 'completed' AND result_json IS NOT NULL AND result_sha256 IS NOT NULL
                     AND ledger_json IS NOT NULL AND ledger_sha256 IS NOT NULL AND completed_at IS NOT NULL)
                ),
                CHECK (
                    (delivery_lease_token IS NULL AND delivery_lease_expires_at IS NULL)
                    OR
                    (delivery_lease_token IS NOT NULL AND delivery_lease_expires_at IS NOT NULL
                     AND sink_delivered = 0)
                )
            )
            """
        )
        instance_id = _initialize_instance_id(connection, from_version=version)
        _upgrade_prerelease_schema(connection, from_version=version)
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS remote_eval_outbox_request
                ON remote_eval_outbox(provider, task_id, request_sha256)
            """
        )
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return instance_id


def connect_database(path: Path, *, expected_instance_id: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=30000")
        actual_instance_id = _read_instance_id(connection)
        if actual_instance_id != _validate_instance_id(expected_instance_id):
            raise RuntimeError("external-evaluation outbox instance identity changed; refusing replaced ledger")
        return connection
    except Exception:
        connection.close()
        raise


def _initialize_instance_id(connection: sqlite3.Connection, *, from_version: int) -> str:
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'remote_eval_outbox_metadata'"
    ).fetchone()
    if table_exists is None:
        if from_version >= SCHEMA_VERSION:
            raise RuntimeError("external-evaluation outbox instance identity metadata is missing")
        connection.execute(
            """
            CREATE TABLE remote_eval_outbox_metadata (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                instance_id TEXT NOT NULL CHECK (
                    length(instance_id) = 64 AND instance_id NOT GLOB '*[^0-9a-f]*'
                )
            )
            """
        )
        connection.execute(
            "INSERT INTO remote_eval_outbox_metadata (singleton, instance_id) VALUES (1, ?)",
            (secrets.token_hex(32),),
        )
    return _read_instance_id(connection)


def _read_instance_id(connection: sqlite3.Connection) -> str:
    try:
        rows = connection.execute("SELECT singleton, instance_id FROM remote_eval_outbox_metadata").fetchall()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("external-evaluation outbox instance identity metadata is unavailable") from exc
    if len(rows) != 1 or rows[0][0] != 1:
        raise RuntimeError("external-evaluation outbox instance identity metadata must contain exactly one row")
    return _validate_instance_id(rows[0][1])


def _validate_instance_id(value: object) -> str:
    if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError("external-evaluation outbox instance identity must be 64 lowercase hexadecimal characters")
    return value


def _upgrade_prerelease_schema(connection: sqlite3.Connection, *, from_version: int) -> None:
    """Upgrade databases created by prerelease versions of schema v1."""

    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(remote_eval_outbox)")}
    if "request_json_sha256" not in columns:
        connection.execute("ALTER TABLE remote_eval_outbox ADD COLUMN request_json_sha256 TEXT")
        rows = connection.execute("SELECT attempt_id, request_json FROM remote_eval_outbox").fetchall()
        for attempt_id, request_json in rows:
            if not isinstance(request_json, str):
                raise ValueError(f"remote evaluation {attempt_id} has invalid request payload storage")
            connection.execute(
                "UPDATE remote_eval_outbox SET request_json_sha256 = ? WHERE attempt_id = ?",
                (sha256(request_json), attempt_id),
            )
    if "delivery_lease_token" not in columns:
        connection.execute("ALTER TABLE remote_eval_outbox ADD COLUMN delivery_lease_token TEXT")
    if "delivery_lease_expires_at" not in columns:
        connection.execute("ALTER TABLE remote_eval_outbox ADD COLUMN delivery_lease_expires_at REAL")

    if from_version >= 2:
        return
    rows = connection.execute(
        """
        SELECT attempt_id, ledger_json, ledger_sha256
          FROM remote_eval_outbox
         WHERE state = 'completed'
        """
    ).fetchall()
    for attempt_id, raw, expected_digest in rows:
        checked = checked_payload(raw, expected_digest, label=f"remote evaluation {attempt_id} ledger")
        payload = load_json(checked, label=f"remote evaluation {attempt_id} ledger")
        if isinstance(payload, dict) and "attempt_id" not in payload:
            payload["attempt_id"] = expect_str(attempt_id, "outbox attempt_id", nonempty=True)
            migrated = canonical_json(payload)
            connection.execute(
                """
                UPDATE remote_eval_outbox
                   SET ledger_json = ?, ledger_sha256 = ?
                 WHERE attempt_id = ?
                """,
                (migrated, sha256(migrated), attempt_id),
            )


def select_claim_row(connection: sqlite3.Connection, attempt_id: str) -> sqlite3.Row | None:
    row = connection.execute(
        """
        SELECT provider, task_id, request_sha256, request_json, request_json_sha256,
               state, result_json, result_sha256, ledger_json, ledger_sha256,
               sink_delivered
          FROM remote_eval_outbox
         WHERE attempt_id = ?
        """,
        (attempt_id,),
    ).fetchone()
    return cast(sqlite3.Row | None, row)


def delivery_was_already_acknowledged(connection: sqlite3.Connection, attempt_id: str) -> bool:
    row = connection.execute(
        "SELECT state, sink_delivered FROM remote_eval_outbox WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    if row is None or expect_str(row["state"], "outbox state") != "completed":
        return False
    return sqlite_bool(row["sink_delivered"], label="outbox sink_delivered")


def claim_identity_matches(
    row: sqlite3.Row,
    *,
    attempt_id: str,
    provider: str,
    task_id: str,
    request_sha256: str,
    request_json: str,
) -> bool:
    """Checksum and compare the stored row and its status projection."""

    stored_request_json = checked_payload(
        row["request_json"],
        row["request_json_sha256"],
        label=f"remote evaluation {attempt_id} request",
    )
    payload = request_status_payload(load_json(stored_request_json, label=f"remote evaluation {attempt_id} request"))
    actual = (
        expect_str(row["provider"], "outbox provider", nonempty=True),
        expect_str(row["task_id"], "outbox task_id", nonempty=True),
        expect_sha256(row["request_sha256"], "outbox request_sha256"),
        stored_request_json,
    )
    expected = (provider, task_id, request_sha256, request_json)
    return actual == expected and (payload["provider"], payload["task_id"], payload["request_sha256"]) == expected[:3]
