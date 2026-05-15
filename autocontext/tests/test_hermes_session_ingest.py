"""AC-706 slice 2: ingest Hermes session DB as ProductionTrace JSONL.

Application-service tests covering:

* end-to-end: 2 sessions, redacted message content, valid PT JSONL,
* per-message content goes through the shared redaction policy
  (reuses slice 1's `RedactionPolicy` — DRY across both ingest paths),
* `--since` filters older sessions; `--limit` caps written traces,
* `--dry-run` produces counts without writing the output,
* missing session DB returns an empty summary (graceful, exit 0),
* schema drift is tolerated end-to-end (slice 1 repo tests cover the
  drill-down; this is a smoke check that the integration still works),
* the importer never writes to the Hermes DB (size + mtime invariant),
* `--redact off` records the raw-content marker in `summary.warnings`,
  matching the slice 1 contract (DRY: same constant),
* CLI subcommand wires through.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from autocontext.hermes.redaction import RedactionPolicy
from autocontext.hermes.session_ingest import (
    SessionIngestSummary,
    ingest_session_db,
)


def _plant_hermes_home_with_sessions(
    home: Path,
    *,
    sessions: list[dict],
    messages: list[dict],
) -> Path:
    """Create <home>/state.db shaped like a Hermes v0.12 session store."""
    home.mkdir(parents=True, exist_ok=True)
    db = home / "state.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, agent_id TEXT, metadata TEXT)"
        )
        conn.execute(
            "CREATE TABLE messages (session_id TEXT, seq INTEGER, role TEXT, content TEXT, timestamp TEXT, metadata TEXT)"
        )
        for s in sessions:
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                (
                    s["session_id"],
                    s.get("started_at"),
                    s.get("ended_at"),
                    s.get("agent_id"),
                    s.get("metadata"),
                ),
            )
        for m in messages:
            conn.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (
                    m["session_id"],
                    m.get("seq", 0),
                    m.get("role", "user"),
                    m.get("content", ""),
                    m.get("timestamp"),
                    m.get("metadata"),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return db


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_two_sessions_emit_two_redacted_production_traces(tmp_path: Path) -> None:
    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[
            {
                "session_id": "s1",
                "started_at": "2026-05-10T10:00:00Z",
                "ended_at": "2026-05-10T10:05:00Z",
                "agent_id": "claude",
                "metadata": '{"topic":"billing"}',
            },
            {
                "session_id": "s2",
                "started_at": "2026-05-11T10:00:00Z",
                "ended_at": "2026-05-11T10:02:00Z",
                "agent_id": "claude",
            },
        ],
        messages=[
            {"session_id": "s1", "seq": 1, "role": "user", "content": "key sk-ant-abcdef1234567890abcdef"},
            {"session_id": "s1", "seq": 2, "role": "assistant", "content": "ack"},
            {"session_id": "s2", "seq": 1, "role": "user", "content": "hello"},
        ],
    )
    output = tmp_path / "sessions.jsonl"

    summary = ingest_session_db(
        home=home,
        output=output,
        policy=RedactionPolicy(mode="standard"),
    )

    assert isinstance(summary, SessionIngestSummary)
    assert summary.sessions_read == 2
    assert summary.traces_written == 2
    rows = _load_jsonl(output)
    # Each row is a ProductionTrace; the first message is system,
    # the second is the redacted user content.
    assert rows[0]["messages"][0]["role"] == "system"
    assert "sk-ant-" not in json.dumps(rows[0])
    assert "[REDACTED_API_KEY]" in json.dumps(rows[0])


def test_messages_pass_through_shared_redaction_policy(tmp_path: Path) -> None:
    """DRY: the session ingester must reuse the slice 1 RedactionPolicy
    so a content-bearing trace is redacted with the same rules as a
    trajectory."""
    import re

    from autocontext.hermes.redaction import UserPattern

    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T10:00:00Z"}],
        messages=[{"session_id": "s1", "seq": 1, "role": "user", "content": "ticket TKT-99"}],
    )
    output = tmp_path / "sessions.jsonl"
    policy = RedactionPolicy(
        mode="strict",
        user_patterns=(UserPattern(name="ticket", pattern=re.compile(r"TKT-\d+")),),
    )
    ingest_session_db(home=home, output=output, policy=policy)
    rows = _load_jsonl(output)
    assert "TKT-99" not in json.dumps(rows[0])
    assert "[REDACTED_USER_PATTERN:ticket]" in json.dumps(rows[0])


def test_since_filter_drops_older_sessions(tmp_path: Path) -> None:
    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[
            {"session_id": "old", "started_at": "2026-04-01T00:00:00Z"},
            {"session_id": "new", "started_at": "2026-05-10T00:00:00Z"},
        ],
        messages=[],
    )
    output = tmp_path / "sessions.jsonl"
    summary = ingest_session_db(
        home=home,
        output=output,
        policy=RedactionPolicy(mode="standard"),
        since="2026-05-01T00:00:00Z",
    )
    assert summary.traces_written == 1
    rows = _load_jsonl(output)
    assert all("old" not in json.dumps(r) for r in rows)


def test_limit_caps_traces_written(tmp_path: Path) -> None:
    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[{"session_id": f"s{i}", "started_at": f"2026-05-{i + 10:02d}T00:00:00Z"} for i in range(5)],
        messages=[],
    )
    output = tmp_path / "sessions.jsonl"
    summary = ingest_session_db(
        home=home,
        output=output,
        policy=RedactionPolicy(mode="standard"),
        limit=2,
    )
    assert summary.traces_written == 2
    assert len(_load_jsonl(output)) == 2


def test_dry_run_reports_counts_without_writing(tmp_path: Path) -> None:
    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[{"session_id": "s1", "seq": 1, "role": "user", "content": "sk-ant-abcdef1234567890abcdef"}],
    )
    output = tmp_path / "sessions.jsonl"
    summary = ingest_session_db(
        home=home,
        output=output,
        policy=RedactionPolicy(mode="standard"),
        dry_run=True,
    )
    assert summary.dry_run is True
    assert summary.traces_written == 1
    assert summary.redactions.total >= 1
    assert not output.exists()


def test_missing_session_db_returns_empty_summary(tmp_path: Path) -> None:
    """A Hermes home without a state.db should yield an empty summary,
    not an error. This matches the AC-706 inspect posture where the
    session DB is optional."""
    home = tmp_path / "hermes"
    home.mkdir()
    output = tmp_path / "sessions.jsonl"
    summary = ingest_session_db(
        home=home,
        output=output,
        policy=RedactionPolicy(mode="standard"),
    )
    assert summary.sessions_read == 0
    assert summary.traces_written == 0
    assert output.exists()
    assert output.read_text(encoding="utf-8") == ""


def test_importer_never_writes_to_session_db(tmp_path: Path) -> None:
    """Per AC-706 acceptance: the importer must never write to the
    Hermes DB. Verify by recording the file's mtime+size before and
    after ingest."""
    home = tmp_path / "hermes"
    db = _plant_hermes_home_with_sessions(
        home,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[{"session_id": "s1", "seq": 1, "role": "user", "content": "hi"}],
    )
    before_mtime = db.stat().st_mtime
    before_size = db.stat().st_size
    output = tmp_path / "sessions.jsonl"
    ingest_session_db(home=home, output=output, policy=RedactionPolicy(mode="standard"))
    assert db.stat().st_mtime == before_mtime
    assert db.stat().st_size == before_size


def test_schema_drift_tolerated_end_to_end(tmp_path: Path) -> None:
    """A Hermes DB with extra columns still ingests cleanly (the
    repository ignores unknown columns)."""
    home = tmp_path / "hermes"
    home.mkdir()
    db = home / "state.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started_at TEXT, future_field TEXT)")
        conn.execute("CREATE TABLE messages (session_id TEXT, seq INTEGER, role TEXT, content TEXT, experimental_field TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('s1', '2026-05-10T00:00:00Z', 'x')")
        conn.execute("INSERT INTO messages VALUES ('s1', 1, 'user', 'hi', 'x')")
        conn.commit()
    finally:
        conn.close()
    output = tmp_path / "sessions.jsonl"
    summary = ingest_session_db(home=home, output=output, policy=RedactionPolicy(mode="standard"))
    assert summary.traces_written == 1


def test_redact_off_records_raw_content_warning(tmp_path: Path) -> None:
    """DRY: the session ingester must surface the same off-mode marker
    as the trajectory ingester. JSON callers and audit logs need a
    consistent opt-in marker."""
    from autocontext.hermes.trajectory_ingest import RAW_CONTENT_WARNING

    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[{"session_id": "s1", "seq": 1, "role": "user", "content": "raw"}],
    )
    output = tmp_path / "sessions.jsonl"
    summary = ingest_session_db(
        home=home,
        output=output,
        policy=RedactionPolicy(mode="off"),
    )
    assert RAW_CONTENT_WARNING in summary.warnings


def test_per_trace_metadata_carries_session_envelope(tmp_path: Path) -> None:
    """The session_id, agent_id, and started/ended_at are load-bearing
    for downstream consumers joining traces to Hermes runs. They must
    land in trace.metadata."""
    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[
            {
                "session_id": "s1",
                "started_at": "2026-05-10T10:00:00Z",
                "ended_at": "2026-05-10T10:05:00Z",
                "agent_id": "claude",
                "metadata": '{"topic":"billing"}',
            }
        ],
        messages=[{"session_id": "s1", "seq": 1, "role": "user", "content": "hi"}],
    )
    output = tmp_path / "sessions.jsonl"
    ingest_session_db(home=home, output=output, policy=RedactionPolicy(mode="standard"))
    rows = _load_jsonl(output)
    assert rows[0]["metadata"]["session_id"] == "s1"
    assert rows[0]["metadata"]["agent_id"] == "claude"
    assert rows[0]["metadata"]["source"] == "hermes.session"
    assert rows[0]["metadata"]["session_metadata"] == {"topic": "billing"}


def test_invalid_since_raises_value_error(tmp_path: Path) -> None:
    """Same boundary contract as slice 1's --since: silently disabling
    on a typo lets every session in. Raise at the boundary instead."""
    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[],
    )
    output = tmp_path / "sessions.jsonl"
    with pytest.raises(ValueError, match="invalid --since"):
        ingest_session_db(
            home=home,
            output=output,
            policy=RedactionPolicy(mode="standard"),
            since="not-a-date",
        )


def test_cli_ingest_sessions_writes_redacted_jsonl(tmp_path: Path) -> None:
    """End-to-end CLI: `autoctx hermes ingest-sessions --redact standard`."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[{"session_id": "s1", "seq": 1, "role": "user", "content": "sk-ant-abcdef1234567890abcdef"}],
    )
    output = tmp_path / "sessions.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "ingest-sessions",
            "--home",
            str(home),
            "--output",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["traces_written"] == 1
    rows = _load_jsonl(output)
    assert "sk-ant-" not in json.dumps(rows[0])


def test_cli_redact_off_includes_raw_warning_in_json(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from autocontext.cli import app
    from autocontext.hermes.trajectory_ingest import RAW_CONTENT_WARNING

    home = tmp_path / "hermes"
    _plant_hermes_home_with_sessions(
        home,
        sessions=[{"session_id": "s1", "started_at": "2026-05-10T00:00:00Z"}],
        messages=[{"session_id": "s1", "seq": 1, "role": "user", "content": "raw"}],
    )
    output = tmp_path / "sessions.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "ingest-sessions",
            "--home",
            str(home),
            "--output",
            str(output),
            "--redact",
            "off",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert RAW_CONTENT_WARNING in payload["warnings"]
