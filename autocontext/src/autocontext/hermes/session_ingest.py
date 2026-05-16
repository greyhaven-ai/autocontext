"""AC-706 slice 2: ingest Hermes session DB as ProductionTrace JSONL.

Application service that:

* opens ``<home>/state.db`` via :class:`HermesSessionRepository`
  (read-only, schema-drift tolerant),
* walks each session in ``started_at`` order, applying ``--since`` /
  ``--limit`` filters,
* runs every message through the shared
  :class:`~autocontext.hermes.redaction.RedactionPolicy` so the
  redaction posture matches slice 1's trajectory ingest (DRY),
* synthesizes a system message that describes the session envelope,
* maps the session into a ProductionTrace via the same
  ``production_traces.emit.build_trace`` helper the curator ingester
  (AC-704) uses (DRY),
* writes JSONL to ``--output`` and returns a structured summary.

A missing ``state.db`` is not an error: the session DB is optional
per AC-706, so callers get an empty summary and exit 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from autocontext.hermes.redaction import RedactionPolicy, RedactionStats, redact_text, redact_value
from autocontext.hermes.sessions import (
    HermesMessage,
    HermesSession,
    HermesSessionRepository,
    SessionDBMissing,
)
from autocontext.hermes.trajectory_ingest import RAW_CONTENT_WARNING
from autocontext.production_traces.emit import build_trace


@dataclass(slots=True)
class SessionIngestSummary:
    """What happened during a single session-ingest invocation."""

    home: Path
    output_path: Path | None
    sessions_read: int = 0
    traces_written: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    redactions: RedactionStats = field(default_factory=RedactionStats)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "home": str(self.home),
            "output_path": str(self.output_path) if self.output_path is not None else None,
            "sessions_read": self.sessions_read,
            "traces_written": self.traces_written,
            "skipped": self.skipped,
            "warnings": list(self.warnings),
            "redactions": self.redactions.to_dict(),
            "dry_run": self.dry_run,
        }


def ingest_session_db(
    *,
    home: Path,
    output: Path,
    policy: RedactionPolicy,
    since: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> SessionIngestSummary:
    """Ingest ``<home>/state.db`` into ProductionTrace JSONL.

    Args:
        home: Hermes home directory (parent of ``state.db``).
        output: JSONL destination. Created with parents if missing.
            Ignored when ``dry_run`` is True; always created (even
            empty) otherwise, so callers can rely on its existence.
        policy: redaction policy (shared with slice 1 trajectory
            ingest).
        since: ISO-8601 timestamp; sessions with ``started_at`` strictly
            before are skipped. Raises ``ValueError`` on invalid input
            (boundary contract matches the rest of the Hermes ingesters).
        limit: cap on number of traces to write.
        dry_run: count and redact without writing output (privacy
            preview per AC-706).

    Returns:
        :class:`SessionIngestSummary` with counts, warnings, and stats.
    """
    summary = SessionIngestSummary(
        home=home,
        output_path=None if dry_run else output,
        dry_run=dry_run,
    )
    if policy.mode == "off":
        summary.warnings.append(RAW_CONTENT_WARNING)

    since_dt: datetime | None = None
    if since is not None:
        since_dt = _parse_iso(since)
        if since_dt is None:
            raise ValueError(f"invalid --since value {since!r}; expected ISO-8601 timestamp")

    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("", encoding="utf-8")

    db_path = home / "state.db"
    try:
        repo = HermesSessionRepository(db_path)
    except SessionDBMissing:
        return summary

    traces: list[dict[str, Any]] = []
    for session in repo.iter_sessions(since=since_dt):
        summary.sessions_read += 1
        if limit is not None and len(traces) >= limit:
            continue
        messages = list(repo.iter_messages(session.session_id))
        trace, per_session_stats = _session_to_trace(
            session=session,
            messages=messages,
            policy=policy,
        )
        for category, count in per_session_stats.by_category.items():
            summary.redactions.add(category, count)
        traces.append(trace)

    summary.traces_written = len(traces)
    if not dry_run and traces:
        with output.open("w", encoding="utf-8") as fh:
            for trace in traces:
                fh.write(json.dumps(trace, separators=(",", ":")) + "\n")
    return summary


def _session_to_trace(
    *,
    session: HermesSession,
    messages: list[HermesMessage],
    policy: RedactionPolicy,
) -> tuple[dict[str, Any], RedactionStats]:
    """Map a HermesSession + its messages into a ProductionTrace.

    Returns ``(trace_dict, per_session_redaction_stats)`` so the
    application service can fold the stats into its summary.
    """
    stats = RedactionStats()
    started_at = session.started_at or _now_iso()
    ended_at = session.ended_at or started_at
    latency_ms = _latency_ms(started_at, ended_at)

    pt_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": _session_summary_text(session, message_count=len(messages)),
            "timestamp": started_at,
        }
    ]
    for msg in messages:
        redacted, sub = redact_text(msg.content, policy)
        for category, count in sub.by_category.items():
            stats.add(category, count)
        pt_messages.append(
            {
                "role": _normalize_role(msg.role),
                "content": redacted,
                "timestamp": msg.timestamp or started_at,
            }
        )

    # PR #968 review (P2): session.metadata is operator-controlled and may
    # carry API keys, bearer tokens, or PII. Route it through the same
    # RedactionPolicy as message content so secrets cannot bypass the
    # ingester via the metadata path.
    redacted_session_metadata, metadata_stats = redact_value(
        dict(session.metadata) if session.metadata else {},
        policy,
    )
    for category, count in metadata_stats.by_category.items():
        stats.add(category, count)
    metadata: dict[str, Any] = {
        "source": "hermes.session",
        "session_id": session.session_id,
        "agent_id": session.agent_id,
        "session_started_at": session.started_at,
        "session_ended_at": session.ended_at,
        "session_metadata": redacted_session_metadata,
    }

    trace = build_trace(
        provider="other",
        model=session.agent_id or "unknown",
        messages=pt_messages,
        timing={
            "startedAt": started_at,
            "endedAt": ended_at,
            "latencyMs": latency_ms,
        },
        usage={"tokensIn": 0, "tokensOut": 0},
        env={"environmentTag": "dev", "appId": "hermes-session"},
        tool_calls=[],
        metadata=metadata,
    )
    return trace, stats


def _session_summary_text(session: HermesSession, *, message_count: int) -> str:
    parts = [
        f"Hermes session {session.session_id}",
        f"agent={session.agent_id or 'unknown'}",
        f"messages={message_count}",
    ]
    if session.started_at:
        parts.append(f"started_at={session.started_at}")
    if session.ended_at:
        parts.append(f"ended_at={session.ended_at}")
    return " ".join(parts) + "."


def _normalize_role(role: str) -> str:
    """ProductionTrace roles are constrained; normalize Hermes role
    strings to the closest match.

    Anything we don't recognize falls back to ``"user"`` so the trace
    still validates.
    """
    role_lc = role.strip().lower() if role else ""
    if role_lc in {"system", "assistant", "user", "tool"}:
        return role_lc
    return "user"


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _latency_ms(started_at: str, ended_at: str) -> int:
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    if start is None or end is None or end < start:
        return 0
    return int((end - start) / timedelta(milliseconds=1))


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


__all__ = ["SessionIngestSummary", "ingest_session_db"]
