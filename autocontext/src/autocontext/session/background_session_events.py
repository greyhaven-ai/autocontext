from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypeAlias

from autocontext.session.runtime_events import RuntimeSessionEvent, RuntimeSessionEventLog

NormalizedSessionEventName: TypeAlias = Literal[
    "session_created",
    "session_queued",
    "executor_starting",
    "executor_ready",
    "prompt_queued",
    "prompt_started",
    "runtime_event",
    "artifact_created",
    "child_session_created",
    "session_status",
    "session_completed",
]
NormalizedSessionEventStatus: TypeAlias = Literal["queued", "running", "completed", "failed", "canceled", "skipped", "unknown"]
NormalizedSessionEventSummaryValue: TypeAlias = str | int | float | bool
NormalizedSessionEvent: TypeAlias = dict[str, Any]


def normalize_background_session_timeline(
    log: RuntimeSessionEventLog,
    *,
    child_logs: Sequence[RuntimeSessionEventLog] | None = None,
) -> list[NormalizedSessionEvent]:
    events = [normalize_runtime_session_event(event) for event in sorted(log.events, key=lambda event: event.sequence)]
    for child_log in child_logs or []:
        events.extend(
            _with_child_lineage(normalize_runtime_session_event(event), child_log)
            for event in sorted(child_log.events, key=lambda event: event.sequence)
        )
    return sorted(events, key=lambda event: (str(event["ts"]), str(event["session_id"]), int(event["sequence"])))


def normalize_runtime_session_event(event: RuntimeSessionEvent) -> NormalizedSessionEvent:
    if event.event_type == "prompt_submitted":
        return _base_event(
            event,
            normalized_event="prompt_started",
            status="running",
            title="Prompt started",
            payload_summary=_pick_payload(event.payload, {"request_id": "requestId", "role": "role"}),
        )
    if event.event_type == "assistant_message":
        return _base_event(
            event,
            normalized_event="runtime_event",
            status=_runtime_action_status(event.payload, "completed"),
            title="Assistant message",
            payload_summary=_pick_payload(event.payload, {"request_id": "requestId", "role": "role"}),
        )
    if event.event_type == "shell_command":
        return _base_event(
            event,
            normalized_event="runtime_event",
            status=_runtime_action_status(event.payload, "running"),
            title="Shell command",
            payload_summary=_pick_payload(event.payload, {"command": "command", "cwd": "cwd", "exit_code": "exitCode"}),
        )
    if event.event_type == "tool_call":
        return _base_event(
            event,
            normalized_event="runtime_event",
            status=_runtime_action_status(event.payload, "completed"),
            title="Tool call",
            payload_summary=_pick_payload(event.payload, {"tool": "tool", "name": "name"}),
        )
    if event.event_type == "child_task_started":
        return _base_event(
            event,
            normalized_event="child_session_created",
            status="running",
            title="Child session created",
            payload_summary=_pick_payload(
                event.payload,
                {"child_session_id": "childSessionId", "role": "role", "task_id": "taskId"},
            ),
        )
    if event.event_type == "child_task_completed":
        canceled = _is_canceled_payload(event.payload)
        failed = event.payload.get("isError") is True
        status: NormalizedSessionEventStatus = "canceled" if canceled else "failed" if failed else "completed"
        title = "Child session canceled" if canceled else "Child session failed" if failed else "Child session completed"
        return _base_event(
            event,
            normalized_event="session_status",
            status=status,
            title=title,
            payload_summary=_pick_payload(event.payload, {"task_id": "taskId"}),
        )
    return _base_event(
        event,
        normalized_event="runtime_event",
        status="completed",
        title="Compaction recorded",
        payload_summary=_pick_payload(event.payload, {"summary_artifact_id": "summaryArtifactId"}),
    )


def build_lifecycle_session_event(
    *,
    session_id: str,
    sequence: int,
    timestamp: str,
    hook: str,
    phase: str,
) -> NormalizedSessionEvent:
    failed = phase in {"failed", "timeout"}
    completed = phase == "completed"
    skipped = phase == "skipped"
    normalized_event: NormalizedSessionEventName
    if completed and hook == "start":
        normalized_event = "executor_ready"
    elif failed or skipped:
        normalized_event = "session_status"
    else:
        normalized_event = "executor_starting"
    return {
        "event_id": f"lifecycle:{session_id}:{hook}:{phase}:{sequence}",
        "session_id": session_id,
        "sequence": sequence,
        "ts": timestamp,
        "event": normalized_event,
        "source_event_type": "lifecycle_hook",
        "status": "skipped" if skipped else "failed" if failed else "completed" if completed else "running",
        "title": f"Lifecycle hook {hook} {phase}",
        "payload_summary": {"hook": hook, "phase": phase},
    }


def build_artifact_created_session_event(
    *,
    session_id: str,
    sequence: int,
    timestamp: str,
    artifact_id: str,
    kind: str,
    label: str | None = None,
    url: str | None = None,
    path: str | None = None,
) -> NormalizedSessionEvent:
    return {
        "event_id": f"artifact:{session_id}:{artifact_id}:{sequence}",
        "session_id": session_id,
        "sequence": sequence,
        "ts": timestamp,
        "event": "artifact_created",
        "source_event_type": "artifact",
        "status": "completed",
        "title": "Artifact created",
        "payload_summary": _sanitize_summary(
            {"artifact_id": artifact_id, "kind": kind, "label": label, "path": path, "url": url}
        ),
    }


def build_session_status_event(
    *,
    session_id: str,
    sequence: int,
    timestamp: str,
    status: NormalizedSessionEventStatus,
    reason: str | None = None,
) -> NormalizedSessionEvent:
    terminal = status in {"completed", "failed", "canceled", "skipped"}
    return {
        "event_id": f"status:{session_id}:{status}:{sequence}",
        "session_id": session_id,
        "sequence": sequence,
        "ts": timestamp,
        "event": "session_completed" if terminal else "session_status",
        "source_event_type": "session_status",
        "status": status,
        "title": f"Session {status}",
        "payload_summary": _sanitize_summary({"reason": reason}),
    }


def _base_event(
    event: RuntimeSessionEvent,
    *,
    normalized_event: NormalizedSessionEventName,
    status: NormalizedSessionEventStatus,
    title: str,
    payload_summary: dict[str, NormalizedSessionEventSummaryValue],
) -> NormalizedSessionEvent:
    return {
        "event_id": event.event_id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "ts": event.timestamp,
        "event": normalized_event,
        "source_event_type": event.event_type.value,
        "status": status,
        "title": title,
        "payload_summary": payload_summary,
    }


def _with_child_lineage(
    event: NormalizedSessionEvent,
    child_log: RuntimeSessionEventLog,
) -> NormalizedSessionEvent:
    payload_summary = dict(event["payload_summary"])
    payload_summary.update(
        _sanitize_summary(
            {
                "child_session_id": child_log.session_id,
                "parent_session_id": child_log.parent_session_id,
                "task_id": child_log.task_id,
                "worker_id": child_log.worker_id,
            }
        )
    )
    return {**event, "payload_summary": payload_summary}


def _pick_payload(
    payload: Mapping[str, Any],
    mapping: Mapping[str, str],
) -> dict[str, NormalizedSessionEventSummaryValue]:
    summary: dict[str, NormalizedSessionEventSummaryValue] = {}
    for output_key, input_key in mapping.items():
        value = payload.get(input_key)
        if isinstance(value, str | int | float | bool):
            summary[output_key] = value
    return summary


def _sanitize_summary(value: Mapping[str, Any]) -> dict[str, NormalizedSessionEventSummaryValue]:
    summary: dict[str, NormalizedSessionEventSummaryValue] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str | int | float | bool):
            summary[key] = item
    return summary


def _runtime_action_status(
    payload: Mapping[str, Any],
    default_status: NormalizedSessionEventStatus,
) -> NormalizedSessionEventStatus:
    if _is_canceled_payload(payload):
        return "canceled"
    if _has_failure_payload(payload):
        return "failed"
    if _has_completed_payload(payload):
        return "completed"
    return default_status


def _is_canceled_payload(payload: Mapping[str, Any]) -> bool:
    return _read_phase(payload) in {"canceled", "cancelled"} or _read_status(payload) in {"canceled", "cancelled"}


def _has_failure_payload(payload: Mapping[str, Any]) -> bool:
    exit_code = _read_exit_code(payload)
    return (
        payload.get("isError") is True
        or (exit_code is not None and exit_code != 0)
        or _read_non_empty_string(payload.get("error")) != ""
        or _read_phase(payload) in {"error", "failed", "failure", "timeout", "timed_out"}
    )


def _has_completed_payload(payload: Mapping[str, Any]) -> bool:
    exit_code = _read_exit_code(payload)
    return exit_code == 0 or _read_phase(payload) in {"completed", "complete", "success", "succeeded"}


def _read_phase(payload: Mapping[str, Any]) -> str:
    return _read_non_empty_string(payload.get("phase")).lower().replace("-", "_")


def _read_status(payload: Mapping[str, Any]) -> str:
    return _read_non_empty_string(payload.get("status")).lower().replace("-", "_")


def _read_non_empty_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _read_exit_code(payload: Mapping[str, Any]) -> int | None:
    value = payload.get("exitCode")
    return value if isinstance(value, int) else None
