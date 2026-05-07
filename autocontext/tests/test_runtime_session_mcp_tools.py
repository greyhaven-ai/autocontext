from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.mcp.tools import MtsToolContext


def _make_ctx(tmp_path: Path) -> MtsToolContext:
    settings = AppSettings(
        knowledge_root=tmp_path / "knowledge",
        runs_root=tmp_path / "runs",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        db_path=tmp_path / "test.sqlite3",
    )
    return MtsToolContext(settings)


def _persist_runtime_session(db_path: Path) -> None:
    from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventStore, RuntimeSessionEventType

    log = RuntimeSessionEventLog.create(
        session_id="run:abc:runtime",
        metadata={"goal": "autoctx run support_triage", "runId": "abc"},
    )
    prompt = log.append(
        RuntimeSessionEventType.PROMPT_SUBMITTED,
        {"requestId": "req-1", "role": "analyst", "prompt": "Analyze the failure"},
    )
    log.append(
        RuntimeSessionEventType.ASSISTANT_MESSAGE,
        {"requestId": "req-1", "promptEventId": prompt.event_id, "role": "analyst", "text": "Found a fix"},
    )
    store = RuntimeSessionEventStore(db_path)
    try:
        store.save(log)
    finally:
        store.close()


def test_mcp_runtime_session_tools_read_summaries_logs_and_timelines(tmp_path: Path) -> None:
    from autocontext.mcp.tools import get_runtime_session, get_runtime_session_timeline, list_runtime_sessions

    ctx = _make_ctx(tmp_path)
    _persist_runtime_session(ctx.settings.db_path)

    assert list_runtime_sessions(ctx, limit=5)["sessions"][0]["session_id"] == "run:abc:runtime"
    assert get_runtime_session(ctx, run_id="abc")["sessionId"] == "run:abc:runtime"
    timeline = get_runtime_session_timeline(ctx, session_id="run:abc:runtime")
    assert timeline["summary"]["session_id"] == "run:abc:runtime"
    assert timeline["items"][0]["response_preview"] == "Found a fix"


def test_mcp_runtime_session_tool_errors_are_structured(tmp_path: Path) -> None:
    from autocontext.mcp.tools import get_runtime_session

    ctx = _make_ctx(tmp_path)

    assert get_runtime_session(ctx)["error"] == "Provide exactly one of session_id or run_id"
    assert get_runtime_session(ctx, session_id="a", run_id="b")["error"] == "Provide exactly one of session_id or run_id"
    assert get_runtime_session(ctx, run_id="missing") == {
        "error": "Runtime session not found",
        "session_id": "run:missing:runtime",
    }
