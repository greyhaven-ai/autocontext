from __future__ import annotations

from typing import Any

from autocontext.session.background_session_read_model import (
    background_session_url,
    build_background_session_detail,
    build_background_session_summary,
    runtime_session_url,
)
from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventType


def _runtime_log() -> RuntimeSessionEventLog:
    return RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "run:run-123:runtime",
            "parentSessionId": "",
            "taskId": "task-123",
            "workerId": "worker-1",
            "metadata": {
                "goal": "autoctx solve billing dispute replies",
                "runId": "run-123",
                "status": "running",
            },
            "createdAt": "2026-06-01T00:00:00.000Z",
            "updatedAt": "2026-06-01T00:01:00.000Z",
            "events": [
                {
                    "eventId": "event-1",
                    "sessionId": "run:run-123:runtime",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED.value,
                    "timestamp": "2026-06-01T00:00:10.000Z",
                    "payload": {
                        "prompt": "Improve billing replies with TOKEN=SECRET_VALUE",
                        "requestId": "req-1",
                    },
                    "parentSessionId": "",
                    "taskId": "task-123",
                    "workerId": "worker-1",
                },
                {
                    "eventId": "event-2",
                    "sessionId": "run:run-123:runtime",
                    "sequence": 1,
                    "eventType": RuntimeSessionEventType.SHELL_COMMAND.value,
                    "timestamp": "2026-06-01T00:00:20.000Z",
                    "payload": {"command": "npm test", "exitCode": 0},
                    "parentSessionId": "",
                    "taskId": "task-123",
                    "workerId": "worker-1",
                },
            ],
        }
    )


def _child_log() -> RuntimeSessionEventLog:
    return RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "task:run:run-123:runtime:child-1",
            "parentSessionId": "run:run-123:runtime",
            "taskId": "child-1",
            "workerId": "worker-child",
            "metadata": {"goal": "Inspect failing test", "runId": "run-123", "status": "completed"},
            "createdAt": "2026-06-01T00:00:30.000Z",
            "updatedAt": "2026-06-01T00:00:45.000Z",
            "events": [],
        }
    )


def _task(**overrides: Any) -> dict[str, Any]:
    task = {
        "id": "task-123",
        "spec_name": "billing_dispute_reply_task",
        "status": "running",
        "priority": 5,
        "config_json": '{"trigger":{"type":"manual","actor":"operator"}}',
        "scheduled_at": None,
        "started_at": "2026-06-01T00:00:05.000Z",
        "completed_at": None,
        "best_score": None,
        "best_output": None,
        "total_rounds": None,
        "met_threshold": 0,
        "result_json": None,
        "error": None,
        "created_at": "2026-06-01T00:00:00.000Z",
        "updated_at": "2026-06-01T00:00:50.000Z",
    }
    task.update(overrides)
    return task


def test_background_session_summary_matches_typescript_contract_without_raw_payloads() -> None:
    summary = build_background_session_summary(
        runtime_session=_runtime_log(),
        task=_task(),
        artifacts=[
            {"artifact_id": "trace-1", "kind": "trace", "label": "Runtime trace", "path": "runs/run-123/trace.jsonl"},
            {"artifact_id": "report-1", "kind": "report", "label": "Run report", "path": "runs/run-123/report.md"},
        ],
        child_sessions=[_child_log()],
    )

    assert summary == {
        "session_id": "run:run-123:runtime",
        "runtime_session_id": "run:run-123:runtime",
        "run_id": "run-123",
        "task_id": "task-123",
        "parent_session_id": "",
        "status": "running",
        "goal": "autoctx solve billing dispute replies",
        "event_count": 2,
        "artifact_count": 2,
        "child_session_count": 1,
        "created_at": "2026-06-01T00:00:00.000Z",
        "updated_at": "2026-06-01T00:01:00.000Z",
        "result_url": "/api/cockpit/background-sessions/run%3Arun-123%3Aruntime",
        "runtime_session_url": "/api/cockpit/runtime-sessions/run%3Arun-123%3Aruntime",
    }
    assert "SECRET_VALUE" not in str(summary)


def test_background_session_summary_represents_queued_work_without_runtime_session() -> None:
    summary = build_background_session_summary(
        task=_task(id="queued-1", status="pending", started_at=None, updated_at="2026-06-01T00:00:10.000Z")
    )

    assert summary == {
        "session_id": "task:queued-1",
        "runtime_session_id": "",
        "run_id": "",
        "task_id": "queued-1",
        "parent_session_id": "",
        "status": "queued",
        "goal": "billing_dispute_reply_task",
        "event_count": 0,
        "artifact_count": 0,
        "child_session_count": 0,
        "created_at": "2026-06-01T00:00:00.000Z",
        "updated_at": "2026-06-01T00:00:10.000Z",
        "result_url": "/api/cockpit/background-sessions/task%3Aqueued-1",
        "runtime_session_url": "",
    }


def test_background_session_detail_sanitizes_artifacts_and_child_summaries() -> None:
    detail = build_background_session_detail(
        runtime_session=_runtime_log(),
        task=_task(),
        artifacts=[
            {
                "artifact_id": "pr-1",
                "kind": "pull_request",
                "label": "Review changes",
                "url": "https://github.example/pr/1",
                "metadata": {"secret": "SECRET_VALUE", "branch": "autoctx/run-123"},
            }
        ],
        child_sessions=[_child_log()],
    )

    assert detail["summary"]["session_id"] == "run:run-123:runtime"
    assert detail["artifacts"] == [
        {
            "artifact_id": "pr-1",
            "kind": "pull_request",
            "label": "Review changes",
            "path": "",
            "url": "https://github.example/pr/1",
        }
    ]
    assert detail["child_sessions"][0]["session_id"] == "task:run:run-123:runtime:child-1"
    assert detail["child_sessions"][0]["parent_session_id"] == "run:run-123:runtime"
    assert detail["child_sessions"][0]["status"] == "completed"
    assert detail["trigger"] == {"type": "manual", "actor": "operator"}
    assert "SECRET_VALUE" not in str(detail)


def test_background_session_url_helpers_match_typescript_contract() -> None:
    assert background_session_url("run:run-123:runtime") == "/api/cockpit/background-sessions/run%3Arun-123%3Aruntime"
    assert runtime_session_url("run:run-123:runtime") == "/api/cockpit/runtime-sessions/run%3Arun-123%3Aruntime"
    assert runtime_session_url("") == ""
