from __future__ import annotations

import json

from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventType


def test_runtime_session_log_to_run_trace_maps_allowlisted_events() -> None:
    from autocontext.analytics.runtime_session_run_trace import runtime_session_log_to_run_trace

    parent_log = RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "run:run-1:runtime",
            "parentSessionId": "",
            "taskId": "",
            "workerId": "",
            "metadata": {
                "runId": "run-1",
                "scenarioName": "grid_ctf",
                "secret": "do-not-export",
            },
            "createdAt": "2026-05-10T10:00:00.000Z",
            "updatedAt": "2026-05-10T10:00:05.000Z",
            "events": [
                {
                    "eventId": "prompt-1",
                    "sessionId": "run:run-1:runtime",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED.value,
                    "timestamp": "2026-05-10T10:00:00.000Z",
                    "payload": {
                        "requestId": "req-1",
                        "role": "analyst",
                        "cwd": "/workspace",
                        "prompt": "secret prompt text",
                    },
                },
                {
                    "eventId": "shell-1",
                    "sessionId": "run:run-1:runtime",
                    "sequence": 1,
                    "eventType": RuntimeSessionEventType.SHELL_COMMAND.value,
                    "timestamp": "2026-05-10T10:00:01.000Z",
                    "payload": {
                        "requestId": "req-1",
                        "promptEventId": "prompt-1",
                        "commandName": "verify",
                        "phase": "end",
                        "cwd": "/workspace",
                        "exitCode": 0,
                        "argsSummary": "verify --quick",
                        "stdout": "do-not-export",
                    },
                },
                {
                    "eventId": "child-start",
                    "sessionId": "run:run-1:runtime",
                    "sequence": 2,
                    "eventType": RuntimeSessionEventType.CHILD_TASK_STARTED.value,
                    "timestamp": "2026-05-10T10:00:02.000Z",
                    "payload": {
                        "taskId": "retry",
                        "childSessionId": "task:run:run-1:runtime:retry:w-1",
                        "workerId": "w-1",
                        "role": "coach",
                        "cwd": "/workspace",
                        "depth": 1,
                    },
                },
                {
                    "eventId": "child-done",
                    "sessionId": "run:run-1:runtime",
                    "sequence": 3,
                    "eventType": RuntimeSessionEventType.CHILD_TASK_COMPLETED.value,
                    "timestamp": "2026-05-10T10:00:04.000Z",
                    "payload": {
                        "taskId": "retry",
                        "childSessionId": "task:run:run-1:runtime:retry:w-1",
                        "workerId": "w-1",
                        "role": "coach",
                        "result": "do-not-export",
                        "isError": False,
                    },
                },
                {
                    "eventId": "cmp-1",
                    "sessionId": "run:run-1:runtime",
                    "sequence": 4,
                    "eventType": RuntimeSessionEventType.COMPACTION.value,
                    "timestamp": "2026-05-10T10:00:05.000Z",
                    "payload": {
                        "runId": "run-1",
                        "entryId": "entry-redacted",
                        "entryIds": ["entry-redacted"],
                        "entryCount": 1,
                        "components": "session_reports",
                        "ledgerPath": "/runs/run-1/compactions.jsonl",
                        "latestEntryPath": "/runs/run-1/compactions.latest",
                        "generation": 2,
                        "summary": "do-not-export",
                    },
                },
            ],
        }
    )
    child_log = RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "task:run:run-1:runtime:retry:w-1",
            "parentSessionId": "run:run-1:runtime",
            "taskId": "retry",
            "workerId": "w-1",
            "metadata": {"role": "coach", "secret": "do-not-export"},
            "createdAt": "2026-05-10T10:00:02.500Z",
            "updatedAt": "2026-05-10T10:00:03.000Z",
            "events": [
                {
                    "eventId": "child-prompt",
                    "sessionId": "task:run:run-1:runtime:retry:w-1",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED.value,
                    "timestamp": "2026-05-10T10:00:02.500Z",
                    "parentSessionId": "run:run-1:runtime",
                    "taskId": "retry",
                    "workerId": "w-1",
                    "payload": {
                        "role": "coach",
                        "prompt": "child prompt text",
                        "cwd": "/workspace",
                    },
                },
                {
                    "eventId": "child-answer",
                    "sessionId": "task:run:run-1:runtime:retry:w-1",
                    "sequence": 1,
                    "eventType": RuntimeSessionEventType.ASSISTANT_MESSAGE.value,
                    "timestamp": "2026-05-10T10:00:03.000Z",
                    "parentSessionId": "run:run-1:runtime",
                    "taskId": "retry",
                    "workerId": "w-1",
                    "payload": {
                        "role": "coach",
                        "text": "child answer text",
                        "metadata": {"secret": "do-not-export"},
                    },
                },
            ],
        }
    )

    trace = runtime_session_log_to_run_trace(parent_log, child_logs=[child_log])

    assert trace.run_id == "run-1"
    assert trace.metadata["scenario"] == "grid_ctf"
    assert trace.created_at == "2026-05-10T10:00:00.000Z"
    assert [event.event_type for event in trace.events] == [
        "runtime_prompt_submitted",
        "runtime_shell_command",
        "runtime_child_task_started",
        "runtime_prompt_submitted",
        "runtime_assistant_message",
        "runtime_child_task_completed",
        "runtime_compaction",
    ]

    prompt_event = trace.events[0]
    assert prompt_event.actor.actor_type == "role"
    assert prompt_event.actor.actor_id == "analyst"
    assert prompt_event.detail["runtime_session_id"] == "run:run-1:runtime"
    assert prompt_event.detail["runtime_event_id"] == "prompt-1"
    assert prompt_event.detail["request_id"] == "req-1"
    assert "prompt" not in prompt_event.detail

    shell_event = trace.events[1]
    assert shell_event.category == "tool_invocation"
    assert shell_event.detail["command_name"] == "verify"
    assert shell_event.detail["exit_code"] == 0
    assert "stdout" not in shell_event.detail

    child_start = trace.events[2]
    assert child_start.detail["task_id"] == "retry"
    assert child_start.detail["worker_id"] == "w-1"
    assert child_start.detail["child_session_id"] == "task:run:run-1:runtime:retry:w-1"

    child_prompt = trace.events[3]
    assert child_prompt.parent_event_id == "runtime-child-start"
    assert child_prompt.detail["parent_session_id"] == "run:run-1:runtime"
    assert child_prompt.detail["task_id"] == "retry"
    assert child_prompt.detail["worker_id"] == "w-1"

    child_done = trace.events[5]
    assert child_done.detail["task_id"] == "retry"
    assert child_done.detail["worker_id"] == "w-1"
    assert child_done.detail["child_session_id"] == "task:run:run-1:runtime:retry:w-1"

    compaction_event = trace.events[-1]
    assert compaction_event.category == "checkpoint"
    assert compaction_event.detail["entry_id"] == "entry-redacted"
    assert compaction_event.detail["entry_ids"] == ["entry-redacted"]
    assert compaction_event.detail["ledger_path"] == "/runs/run-1/compactions.jsonl"
    assert compaction_event.resources[0].resource_type == "artifact"
    assert compaction_event.resources[0].resource_id == "entry-redacted"

    serialized = json.dumps(trace.to_dict(), sort_keys=True)
    assert "do-not-export" not in serialized
    assert "secret prompt text" not in serialized
    assert "child answer text" not in serialized


def test_runtime_session_log_to_run_trace_correlates_concurrent_prompt_responses() -> None:
    from autocontext.analytics.runtime_session_run_trace import runtime_session_log_to_run_trace

    log = RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "run:run-2:runtime",
            "metadata": {"runId": "run-2", "scenarioName": "grid_ctf"},
            "createdAt": "2026-05-10T10:00:00.000Z",
            "updatedAt": "2026-05-10T10:00:03.000Z",
            "events": [
                {
                    "eventId": "prompt-a",
                    "sessionId": "run:run-2:runtime",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED.value,
                    "timestamp": "2026-05-10T10:00:00.000Z",
                    "payload": {
                        "requestId": "req-a",
                        "role": "analyst",
                        "prompt": "prompt a",
                    },
                },
                {
                    "eventId": "prompt-b",
                    "sessionId": "run:run-2:runtime",
                    "sequence": 1,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED.value,
                    "timestamp": "2026-05-10T10:00:01.000Z",
                    "payload": {
                        "requestId": "req-b",
                        "role": "coach",
                        "prompt": "prompt b",
                    },
                },
                {
                    "eventId": "assistant-b",
                    "sessionId": "run:run-2:runtime",
                    "sequence": 2,
                    "eventType": RuntimeSessionEventType.ASSISTANT_MESSAGE.value,
                    "timestamp": "2026-05-10T10:00:02.000Z",
                    "payload": {
                        "requestId": "req-b",
                        "promptEventId": "prompt-b",
                        "role": "coach",
                        "text": "answer b",
                    },
                },
                {
                    "eventId": "assistant-a",
                    "sessionId": "run:run-2:runtime",
                    "sequence": 3,
                    "eventType": RuntimeSessionEventType.ASSISTANT_MESSAGE.value,
                    "timestamp": "2026-05-10T10:00:03.000Z",
                    "payload": {
                        "requestId": "req-a",
                        "promptEventId": "prompt-a",
                        "role": "analyst",
                        "text": "answer a",
                    },
                },
            ],
        }
    )

    trace = runtime_session_log_to_run_trace(log)
    by_id = {event.event_id: event for event in trace.events}

    assert by_id["runtime-assistant-b"].parent_event_id == "runtime-prompt-b"
    assert by_id["runtime-assistant-a"].parent_event_id == "runtime-prompt-a"
    assert by_id["runtime-assistant-a"].detail["prompt_event_id"] == "prompt-a"
    assert by_id["runtime-assistant-a"].detail["request_id"] == "req-a"
