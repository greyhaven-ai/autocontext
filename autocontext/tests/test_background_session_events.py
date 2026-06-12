from __future__ import annotations

from autocontext.session.background_session_events import (
    build_artifact_created_session_event,
    build_lifecycle_session_event,
    build_session_status_event,
    normalize_background_session_timeline,
)
from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventType


def _runtime_log() -> RuntimeSessionEventLog:
    return RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "run:run-123:runtime",
            "parentSessionId": "",
            "taskId": "task-123",
            "workerId": "worker-1",
            "metadata": {"goal": "autoctx solve billing dispute replies", "runId": "run-123"},
            "createdAt": "2026-06-01T00:00:00.000Z",
            "updatedAt": "2026-06-01T00:00:40.000Z",
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
                        "role": "competitor",
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
                    "payload": {"command": "npm test", "exitCode": 0, "cwd": "/workspace"},
                    "parentSessionId": "",
                    "taskId": "task-123",
                    "workerId": "worker-1",
                },
                {
                    "eventId": "event-3",
                    "sessionId": "run:run-123:runtime",
                    "sequence": 2,
                    "eventType": RuntimeSessionEventType.CHILD_TASK_STARTED.value,
                    "timestamp": "2026-06-01T00:00:30.000Z",
                    "payload": {
                        "taskId": "child-1",
                        "childSessionId": "task:run:run-123:runtime:child-1",
                        "role": "analyst",
                    },
                    "parentSessionId": "",
                    "taskId": "task-123",
                    "workerId": "worker-1",
                },
                {
                    "eventId": "event-4",
                    "sessionId": "run:run-123:runtime",
                    "sequence": 3,
                    "eventType": RuntimeSessionEventType.CHILD_TASK_COMPLETED.value,
                    "timestamp": "2026-06-01T00:00:40.000Z",
                    "payload": {"taskId": "child-1", "isError": True, "result": "SECRET_VALUE"},
                    "parentSessionId": "",
                    "taskId": "task-123",
                    "workerId": "worker-1",
                },
            ],
        }
    )


def test_background_session_events_match_typescript_contract_without_raw_payloads() -> None:
    events = normalize_background_session_timeline(_runtime_log())

    assert events == [
        {
            "event_id": "event-1",
            "session_id": "run:run-123:runtime",
            "sequence": 0,
            "ts": "2026-06-01T00:00:10.000Z",
            "event": "prompt_started",
            "source_event_type": "prompt_submitted",
            "status": "running",
            "title": "Prompt started",
            "payload_summary": {"request_id": "req-1", "role": "competitor"},
        },
        {
            "event_id": "event-2",
            "session_id": "run:run-123:runtime",
            "sequence": 1,
            "ts": "2026-06-01T00:00:20.000Z",
            "event": "runtime_event",
            "source_event_type": "shell_command",
            "status": "completed",
            "title": "Shell command",
            "payload_summary": {"command": "npm test", "cwd": "/workspace", "exit_code": 0},
        },
        {
            "event_id": "event-3",
            "session_id": "run:run-123:runtime",
            "sequence": 2,
            "ts": "2026-06-01T00:00:30.000Z",
            "event": "child_session_created",
            "source_event_type": "child_task_started",
            "status": "running",
            "title": "Child session created",
            "payload_summary": {
                "child_session_id": "task:run:run-123:runtime:child-1",
                "role": "analyst",
                "task_id": "child-1",
            },
        },
        {
            "event_id": "event-4",
            "session_id": "run:run-123:runtime",
            "sequence": 3,
            "ts": "2026-06-01T00:00:40.000Z",
            "event": "session_status",
            "source_event_type": "child_task_completed",
            "status": "failed",
            "title": "Child session failed",
            "payload_summary": {"task_id": "child-1"},
        },
    ]
    assert "SECRET_VALUE" not in str(events)


def test_parent_background_session_timeline_includes_child_session_events() -> None:
    child = RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "task:run:run-123:runtime:child-1",
            "parentSessionId": "run:run-123:runtime",
            "taskId": "child-1",
            "workerId": "worker-child",
            "metadata": {"goal": "Inspect failing test", "status": "failed"},
            "createdAt": "2026-06-01T00:00:31.000Z",
            "updatedAt": "2026-06-01T00:00:36.000Z",
            "events": [
                {
                    "eventId": "child-prompt",
                    "sessionId": "task:run:run-123:runtime:child-1",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED.value,
                    "timestamp": "2026-06-01T00:00:35.000Z",
                    "payload": {"role": "analyst", "prompt": "SECRET_VALUE"},
                    "parentSessionId": "run:run-123:runtime",
                    "taskId": "child-1",
                    "workerId": "worker-child",
                },
                {
                    "eventId": "child-answer",
                    "sessionId": "task:run:run-123:runtime:child-1",
                    "sequence": 1,
                    "eventType": RuntimeSessionEventType.ASSISTANT_MESSAGE.value,
                    "timestamp": "2026-06-01T00:00:36.000Z",
                    "payload": {"role": "analyst", "isError": True, "error": "SECRET_VALUE"},
                    "parentSessionId": "run:run-123:runtime",
                    "taskId": "child-1",
                    "workerId": "worker-child",
                },
            ],
        }
    )

    events = normalize_background_session_timeline(_runtime_log(), child_logs=[child])

    assert [event["event_id"] for event in events] == [
        "event-1",
        "event-2",
        "event-3",
        "child-prompt",
        "child-answer",
        "event-4",
    ]
    assert events[3]["payload_summary"] == {
        "role": "analyst",
        "child_session_id": "task:run:run-123:runtime:child-1",
        "parent_session_id": "run:run-123:runtime",
        "task_id": "child-1",
        "worker_id": "worker-child",
    }
    assert events[4]["status"] == "failed"
    assert events[4]["payload_summary"] == {
        "role": "analyst",
        "child_session_id": "task:run:run-123:runtime:child-1",
        "parent_session_id": "run:run-123:runtime",
        "task_id": "child-1",
        "worker_id": "worker-child",
    }
    assert "SECRET_VALUE" not in str(events)


def test_failed_runtime_payloads_from_assistants_and_grants_are_marked_failed() -> None:
    log = RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "run:failed-runtime:runtime",
            "parentSessionId": "",
            "taskId": "task-failed",
            "workerId": "worker-1",
            "metadata": {"goal": "autoctx run failed grants", "runId": "failed-runtime"},
            "createdAt": "2026-06-01T01:00:00.000Z",
            "updatedAt": "2026-06-01T01:00:04.000Z",
            "events": [
                {
                    "eventId": "assistant-failed",
                    "sessionId": "run:failed-runtime:runtime",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.ASSISTANT_MESSAGE.value,
                    "timestamp": "2026-06-01T01:00:01.000Z",
                    "payload": {
                        "requestId": "req-failed",
                        "role": "competitor",
                        "isError": True,
                        "error": "SECRET_VALUE",
                    },
                    "parentSessionId": "",
                    "taskId": "task-failed",
                    "workerId": "worker-1",
                },
                {
                    "eventId": "assistant-canceled",
                    "sessionId": "run:failed-runtime:runtime",
                    "sequence": 1,
                    "eventType": RuntimeSessionEventType.ASSISTANT_MESSAGE.value,
                    "timestamp": "2026-06-01T01:00:01.500Z",
                    "payload": {"phase": "canceled", "isError": True, "error": "SECRET_VALUE"},
                    "parentSessionId": "",
                    "taskId": "task-failed",
                    "workerId": "worker-1",
                },
                {
                    "eventId": "tool-failed",
                    "sessionId": "run:failed-runtime:runtime",
                    "sequence": 2,
                    "eventType": RuntimeSessionEventType.TOOL_CALL.value,
                    "timestamp": "2026-06-01T01:00:02.000Z",
                    "payload": {"tool": "workspace.write", "phase": "error", "error": "SECRET_VALUE"},
                    "parentSessionId": "",
                    "taskId": "task-failed",
                    "workerId": "worker-1",
                },
                {
                    "eventId": "shell-failed",
                    "sessionId": "run:failed-runtime:runtime",
                    "sequence": 3,
                    "eventType": RuntimeSessionEventType.SHELL_COMMAND.value,
                    "timestamp": "2026-06-01T01:00:03.000Z",
                    "payload": {
                        "command": "npm test",
                        "cwd": "/workspace",
                        "phase": "error",
                        "error": "SECRET_VALUE",
                    },
                    "parentSessionId": "",
                    "taskId": "task-failed",
                    "workerId": "worker-1",
                },
                {
                    "eventId": "child-canceled",
                    "sessionId": "run:failed-runtime:runtime",
                    "sequence": 4,
                    "eventType": RuntimeSessionEventType.CHILD_TASK_COMPLETED.value,
                    "timestamp": "2026-06-01T01:00:04.000Z",
                    "payload": {"taskId": "child-1", "phase": "canceled", "isError": True, "error": "SECRET_VALUE"},
                    "parentSessionId": "",
                    "taskId": "task-failed",
                    "workerId": "worker-1",
                },
            ],
        }
    )

    events = normalize_background_session_timeline(log)

    assert [(event["event_id"], event["status"], event["payload_summary"]) for event in events] == [
        ("assistant-failed", "failed", {"request_id": "req-failed", "role": "competitor"}),
        ("assistant-canceled", "canceled", {}),
        ("tool-failed", "failed", {"tool": "workspace.write"}),
        ("shell-failed", "failed", {"command": "npm test", "cwd": "/workspace"}),
        ("child-canceled", "canceled", {"task_id": "child-1"}),
    ]
    assert "SECRET_VALUE" not in str(events)


def test_background_session_builders_match_typescript_contract() -> None:
    assert build_lifecycle_session_event(
        session_id="run:run-123:runtime",
        sequence=10,
        timestamp="2026-06-01T00:02:00.000Z",
        hook="setup",
        phase="started",
    ) == {
        "event_id": "lifecycle:run:run-123:runtime:setup:started:10",
        "session_id": "run:run-123:runtime",
        "sequence": 10,
        "ts": "2026-06-01T00:02:00.000Z",
        "event": "executor_starting",
        "source_event_type": "lifecycle_hook",
        "status": "running",
        "title": "Lifecycle hook setup started",
        "payload_summary": {"hook": "setup", "phase": "started"},
    }

    assert (
        build_lifecycle_session_event(
            session_id="run:run-123:runtime",
            sequence=11,
            timestamp="2026-06-01T00:02:10.000Z",
            hook="start",
            phase="completed",
        )["event"]
        == "executor_ready"
    )

    assert build_artifact_created_session_event(
        session_id="run:run-123:runtime",
        sequence=12,
        timestamp="2026-06-01T00:03:00.000Z",
        artifact_id="report-1",
        kind="report",
        label="Run report",
        url="https://example.invalid/report",
    ) == {
        "event_id": "artifact:run:run-123:runtime:report-1:12",
        "session_id": "run:run-123:runtime",
        "sequence": 12,
        "ts": "2026-06-01T00:03:00.000Z",
        "event": "artifact_created",
        "source_event_type": "artifact",
        "status": "completed",
        "title": "Artifact created",
        "payload_summary": {
            "artifact_id": "report-1",
            "kind": "report",
            "label": "Run report",
            "url": "https://example.invalid/report",
        },
    }

    assert (
        build_session_status_event(
            session_id="run:run-123:runtime",
            sequence=13,
            timestamp="2026-06-01T00:04:00.000Z",
            status="completed",
        )["event"]
        == "session_completed"
    )
