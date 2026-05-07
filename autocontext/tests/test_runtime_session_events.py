from __future__ import annotations

from pathlib import Path


def _concurrent_log():
    from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventType

    return RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "run:abc:runtime",
            "parentSessionId": "",
            "taskId": "",
            "workerId": "",
            "metadata": {"goal": "autoctx run support_triage", "runId": "abc"},
            "createdAt": "2026-04-10T00:00:00.000Z",
            "updatedAt": "2026-04-10T00:00:04.000Z",
            "events": [
                {
                    "eventId": "event-1",
                    "sessionId": "run:abc:runtime",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED,
                    "timestamp": "2026-04-10T00:00:01.000Z",
                    "payload": {
                        "requestId": "analyst-request",
                        "role": "analyst",
                        "prompt": "Analyze the failure",
                        "cwd": "/workspace",
                    },
                    "parentSessionId": "",
                    "taskId": "",
                    "workerId": "",
                },
                {
                    "eventId": "event-2",
                    "sessionId": "run:abc:runtime",
                    "sequence": 1,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED,
                    "timestamp": "2026-04-10T00:00:02.000Z",
                    "payload": {
                        "requestId": "coach-request",
                        "role": "coach",
                        "prompt": "Review the patch",
                        "cwd": "/workspace",
                    },
                    "parentSessionId": "",
                    "taskId": "",
                    "workerId": "",
                },
                {
                    "eventId": "event-3",
                    "sessionId": "run:abc:runtime",
                    "sequence": 2,
                    "eventType": RuntimeSessionEventType.ASSISTANT_MESSAGE,
                    "timestamp": "2026-04-10T00:00:03.000Z",
                    "payload": {
                        "requestId": "coach-request",
                        "role": "coach",
                        "text": "Coach response",
                        "cwd": "/workspace",
                    },
                    "parentSessionId": "",
                    "taskId": "",
                    "workerId": "",
                },
                {
                    "eventId": "event-4",
                    "sessionId": "run:abc:runtime",
                    "sequence": 3,
                    "eventType": RuntimeSessionEventType.ASSISTANT_MESSAGE,
                    "timestamp": "2026-04-10T00:00:04.000Z",
                    "payload": {
                        "requestId": "analyst-request",
                        "role": "analyst",
                        "text": "Analyst response",
                        "cwd": "/workspace",
                    },
                    "parentSessionId": "",
                    "taskId": "",
                    "workerId": "",
                },
            ],
        }
    )


def test_runtime_session_event_log_uses_typescript_compatible_json_shape() -> None:
    from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventType

    log = RuntimeSessionEventLog.create(
        session_id="run:abc:runtime",
        metadata={"goal": "autoctx run support_triage", "runId": "abc"},
    )
    prompt = log.append(
        RuntimeSessionEventType.PROMPT_SUBMITTED,
        {"requestId": "req-1", "role": "analyst", "prompt": "Analyze"},
    )
    log.append(
        RuntimeSessionEventType.ASSISTANT_MESSAGE,
        {"requestId": "req-1", "promptEventId": prompt.event_id, "text": "Done"},
    )

    payload = log.to_dict()

    assert payload["sessionId"] == "run:abc:runtime"
    assert payload["metadata"]["goal"] == "autoctx run support_triage"
    assert payload["events"][0]["eventId"] == prompt.event_id
    assert payload["events"][0]["eventType"] == "prompt_submitted"
    assert payload["events"][1]["payload"]["promptEventId"] == prompt.event_id

    restored = RuntimeSessionEventLog.from_dict(payload)
    assert restored.session_id == log.session_id
    assert restored.events[1].payload["requestId"] == "req-1"


def test_runtime_session_event_store_round_trips_logs_and_children(tmp_path: Path) -> None:
    from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventStore, RuntimeSessionEventType

    parent = RuntimeSessionEventLog.create(
        session_id="run:abc:runtime",
        metadata={"goal": "autoctx run support_triage", "runId": "abc"},
    )
    parent.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {"prompt": "Parent"})
    parent.updated_at = "2026-04-10T00:00:01.000Z"
    child = RuntimeSessionEventLog.create(
        session_id="task:run:abc:runtime:task-1",
        parent_session_id="run:abc:runtime",
        task_id="task-1",
        worker_id="worker-1",
        metadata={"goal": "child task"},
    )
    child.append(RuntimeSessionEventType.ASSISTANT_MESSAGE, {"text": "Child done"})
    child.updated_at = "2026-04-10T00:00:02.000Z"

    store = RuntimeSessionEventStore(tmp_path / "runtime-events.db")
    try:
        store.save(parent)
        store.save(child)

        loaded = store.load("run:abc:runtime")
        assert loaded is not None
        assert loaded.to_dict()["events"][0]["eventType"] == "prompt_submitted"
        assert [entry.session_id for entry in store.list(limit=5)] == [
            "task:run:abc:runtime:task-1",
            "run:abc:runtime",
        ]
        assert [entry.session_id for entry in store.list_children("run:abc:runtime")] == [
            "task:run:abc:runtime:task-1"
        ]
    finally:
        store.close()


def test_runtime_session_event_store_closes_operation_connections(tmp_path: Path, monkeypatch) -> None:
    from autocontext.session import runtime_events
    from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventStore, RuntimeSessionEventType

    real_connect = runtime_events.sqlite3.connect
    closed_connections = 0

    class TrackedConnection:
        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, exc_type, exc, traceback):
            return self._conn.__exit__(exc_type, exc, traceback)

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

        def __setattr__(self, name: str, value) -> None:
            if name == "_conn":
                object.__setattr__(self, name, value)
                return
            setattr(self._conn, name, value)

        def close(self) -> None:
            nonlocal closed_connections
            closed_connections += 1
            self._conn.close()

    def connect(*args, **kwargs):
        return TrackedConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(runtime_events.sqlite3, "connect", connect)
    log = RuntimeSessionEventLog.create(session_id="run:abc:runtime")
    log.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {"prompt": "Analyze"})

    store = RuntimeSessionEventStore(tmp_path / "runtime-events.db")
    try:
        store.save(log)

        assert store.load(log.session_id) is not None
        assert [entry.session_id for entry in store.list(limit=1)] == [log.session_id]
    finally:
        store.close()

    assert closed_connections >= 4


def test_runtime_session_read_model_resolves_run_ids_and_summaries() -> None:
    from autocontext.session.runtime_session_ids import runtime_session_id_for_run
    from autocontext.session.runtime_session_read_model import (
        read_runtime_session_by_run_id,
        read_runtime_session_summaries,
        summarize_runtime_session,
    )

    log = _concurrent_log()
    store = {"run:abc:runtime": log}

    assert runtime_session_id_for_run("abc") == "run:abc:runtime"
    assert summarize_runtime_session(log) == {
        "session_id": "run:abc:runtime",
        "parent_session_id": "",
        "task_id": "",
        "worker_id": "",
        "goal": "autoctx run support_triage",
        "event_count": 4,
        "created_at": "2026-04-10T00:00:00.000Z",
        "updated_at": "2026-04-10T00:00:04.000Z",
    }
    assert read_runtime_session_by_run_id(store, "abc") is log
    assert read_runtime_session_summaries(store, limit=10)[0]["session_id"] == "run:abc:runtime"


def test_runtime_session_timeline_pairs_concurrent_responses_by_request_id() -> None:
    from autocontext.session.runtime_session_timeline import build_runtime_session_timeline

    timeline = build_runtime_session_timeline(_concurrent_log())

    assert timeline["items"] == [
        {
            "kind": "prompt",
            "status": "completed",
            "sequence_start": 0,
            "sequence_end": 3,
            "started_at": "2026-04-10T00:00:01.000Z",
            "completed_at": "2026-04-10T00:00:04.000Z",
            "role": "analyst",
            "cwd": "/workspace",
            "prompt_preview": "Analyze the failure",
            "response_preview": "Analyst response",
            "error": "",
            "request_id": "analyst-request",
            "prompt_event_id": "event-1",
            "response_event_id": "event-4",
        },
        {
            "kind": "prompt",
            "status": "completed",
            "sequence_start": 1,
            "sequence_end": 2,
            "started_at": "2026-04-10T00:00:02.000Z",
            "completed_at": "2026-04-10T00:00:03.000Z",
            "role": "coach",
            "cwd": "/workspace",
            "prompt_preview": "Review the patch",
            "response_preview": "Coach response",
            "error": "",
            "request_id": "coach-request",
            "prompt_event_id": "event-2",
            "response_event_id": "event-3",
        },
    ]


def test_runtime_session_timeline_keeps_unmatched_correlated_response_generic() -> None:
    from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventType
    from autocontext.session.runtime_session_timeline import build_runtime_session_timeline

    log = RuntimeSessionEventLog.create(session_id="run:abc:runtime")
    log.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {"requestId": "prompt-request", "prompt": "Analyze"})
    unmatched = log.append(
        RuntimeSessionEventType.ASSISTANT_MESSAGE,
        {"requestId": "other-request", "text": "Unmatched response"},
    )

    timeline = build_runtime_session_timeline(log)

    assert timeline["items"][0]["kind"] == "prompt"
    assert timeline["items"][0]["status"] == "in_flight"
    assert timeline["items"][1]["kind"] == "event"
    assert timeline["items"][1]["event_id"] == unmatched.event_id


def test_runtime_session_timeline_pairs_child_completions_by_child_session_id() -> None:
    from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventType
    from autocontext.session.runtime_session_timeline import build_runtime_session_timeline

    log = RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "run:abc:runtime",
            "createdAt": "2026-04-10T00:00:00.000Z",
            "updatedAt": "2026-04-10T00:00:03.000Z",
            "events": [
                {
                    "eventId": "event-1",
                    "sessionId": "run:abc:runtime",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.CHILD_TASK_STARTED,
                    "timestamp": "2026-04-10T00:00:01.000Z",
                    "payload": {"taskId": "retry", "childSessionId": "c1", "workerId": "worker-1"},
                },
                {
                    "eventId": "event-2",
                    "sessionId": "run:abc:runtime",
                    "sequence": 1,
                    "eventType": RuntimeSessionEventType.CHILD_TASK_STARTED,
                    "timestamp": "2026-04-10T00:00:02.000Z",
                    "payload": {"taskId": "retry", "childSessionId": "c2", "workerId": "worker-2"},
                },
                {
                    "eventId": "event-3",
                    "sessionId": "run:abc:runtime",
                    "sequence": 2,
                    "eventType": RuntimeSessionEventType.CHILD_TASK_COMPLETED,
                    "timestamp": "2026-04-10T00:00:03.000Z",
                    "payload": {"taskId": "retry", "childSessionId": "c1", "result": "c1 done"},
                },
            ],
        }
    )

    timeline = build_runtime_session_timeline(log)
    first, second = timeline["items"]

    assert first["child_session_id"] == "c1"
    assert first["status"] == "completed"
    assert first["sequence_end"] == 2
    assert first["result_preview"] == "c1 done"
    assert second["child_session_id"] == "c2"
    assert second["status"] == "started"
    assert second["sequence_end"] is None
    assert timeline["in_flight_count"] == 1
