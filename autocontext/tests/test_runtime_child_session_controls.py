from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from autocontext.session import RuntimeChildTaskHandlerOutput, RuntimeSession  # type: ignore[import-untyped]
from autocontext.session.runtime_events import (  # type: ignore[import-untyped]
    RuntimeSessionEventLog,
    RuntimeSessionEventStore,
    RuntimeSessionEventType,
)


def test_runtime_session_child_task_concurrency_limit_is_recorded(tmp_path: Path) -> None:
    store = RuntimeSessionEventStore(tmp_path / "runtime-events.db")
    try:
        session = cast(Any, RuntimeSession).create(
            session_id="run:abc:runtime",
            goal="autoctx run support_triage",
            event_store=store,
            max_concurrent_child_tasks=1,
        )
        session.log.append(
            RuntimeSessionEventType.CHILD_TASK_STARTED,
            {"taskId": "active", "childSessionId": "child-active", "workerId": "worker-active"},
        )
        session.save()
        called = False

        def handler(_input: Any) -> RuntimeChildTaskHandlerOutput:
            nonlocal called
            called = True
            return RuntimeChildTaskHandlerOutput(text="should not run")

        result = session.run_child_task(
            prompt="Queue one more",
            role="analyst",
            task_id="queued",
            handler=handler,
        )

        assert called is False
        assert result.is_error is True
        assert result.error == "Maximum concurrent child sessions (1) exceeded"
        parent = store.load("run:abc:runtime")
        child = store.load(result.child_session_id)
        assert parent is not None
        assert child is not None
        assert parent.events[-1].event_type == RuntimeSessionEventType.CHILD_TASK_COMPLETED
        assert parent.events[-1].payload["isError"] is True
        assert parent.events[-1].payload["error"] == "Maximum concurrent child sessions (1) exceeded"
        assert child.metadata["status"] == "failed"
        assert child.events[-1].payload["isError"] is True
    finally:
        store.close()


def test_runtime_session_does_not_overwrite_child_sessions_canceled_during_handler(tmp_path: Path) -> None:
    store = RuntimeSessionEventStore(tmp_path / "runtime-events.db")
    try:
        session = RuntimeSession.create(
            session_id="run:abc:runtime",
            goal="autoctx run support_triage",
            event_store=store,
        )

        def handler(input: Any) -> RuntimeChildTaskHandlerOutput:
            cast(Any, session).cancel_child_session(
                child_session_id=input.child_session_id,
                reason="operator requested",
            )
            return RuntimeChildTaskHandlerOutput(text="late success")

        result = session.run_child_task(
            prompt="Do child work",
            role="analyst",
            task_id="child",
            handler=handler,
        )

        assert result.child_session_id.startswith("task:run:abc:runtime:child:")
        assert result.is_error is True
        assert result.error == "operator requested"
        assert result.text == ""
        parent = store.load("run:abc:runtime")
        canceled_child = store.load(result.child_session_id)
        assert parent is not None
        assert canceled_child is not None
        completions = [
            event
            for event in parent.events
            if event.event_type == RuntimeSessionEventType.CHILD_TASK_COMPLETED
            and event.payload.get("childSessionId") == result.child_session_id
        ]
        assert len(completions) == 1
        assert completions[0].payload["phase"] == "canceled"
        assert completions[0].payload["status"] == "canceled"
        assert completions[0].payload["error"] == "operator requested"
        assert canceled_child.metadata["status"] == "canceled"
        assert session.coordinator.active_workers == []
        assert "late success" not in str(canceled_child.to_dict())
    finally:
        store.close()


def test_runtime_session_can_cancel_active_child_session(tmp_path: Path) -> None:
    store = RuntimeSessionEventStore(tmp_path / "runtime-events.db")
    try:
        session = RuntimeSession.create(
            session_id="run:abc:runtime",
            goal="autoctx run support_triage",
            event_store=store,
        )
        child = RuntimeSessionEventLog.create(
            session_id="task:run:abc:runtime:child:worker-1",
            parent_session_id="run:abc:runtime",
            task_id="child",
            worker_id="worker-1",
            metadata={"goal": "child work", "status": "running"},
        )
        child.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {"prompt": "SECRET_VALUE", "role": "analyst"})
        store.save(child)
        session.log.append(
            RuntimeSessionEventType.CHILD_TASK_STARTED,
            {
                "taskId": "child",
                "childSessionId": child.session_id,
                "workerId": "worker-1",
                "role": "analyst",
            },
        )
        session.save()

        cancellation = cast(Any, session).cancel_child_session(
            child_session_id=child.session_id,
            reason="operator requested",
        )

        assert cancellation.child_session_id == child.session_id
        assert cancellation.status == "canceled"
        assert cancellation.reason == "operator requested"
        parent = store.load("run:abc:runtime")
        canceled_child = store.load(child.session_id)
        assert parent is not None
        assert canceled_child is not None
        assert canceled_child.metadata["status"] == "canceled"
        assert canceled_child.events[-1].event_type == RuntimeSessionEventType.ASSISTANT_MESSAGE
        assert canceled_child.events[-1].payload == {
            "text": "",
            "error": "operator requested",
            "isError": True,
            "phase": "canceled",
            "status": "canceled",
        }
        assert parent.events[-1].event_type == RuntimeSessionEventType.CHILD_TASK_COMPLETED
        assert parent.events[-1].payload == {
            "taskId": "child",
            "childSessionId": child.session_id,
            "workerId": "worker-1",
            "result": "",
            "error": "operator requested",
            "isError": True,
            "phase": "canceled",
            "status": "canceled",
        }
    finally:
        store.close()
