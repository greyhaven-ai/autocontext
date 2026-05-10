from __future__ import annotations

import json
from typing import Any

from autocontext.session.runtime_events import RuntimeSessionEvent, RuntimeSessionEventLog, RuntimeSessionEventType
from autocontext.session.runtime_session_ids import runtime_session_id_for_run
from autocontext.session.runtime_session_read_model import RuntimeSessionReadStore, summarize_runtime_session

RuntimeSessionTimelineItem = dict[str, Any]
RuntimeSessionTimeline = dict[str, Any]


def build_runtime_session_timeline(log: RuntimeSessionEventLog) -> RuntimeSessionTimeline:
    items: list[RuntimeSessionTimelineItem] = []
    open_prompts: list[RuntimeSessionTimelineItem] = []
    prompts_by_request_id: dict[str, RuntimeSessionTimelineItem] = {}
    prompts_by_event_id: dict[str, RuntimeSessionTimelineItem] = {}
    child_tasks_by_correlation_key: dict[str, RuntimeSessionTimelineItem] = {}

    for event in log.events:
        if event.event_type == RuntimeSessionEventType.PROMPT_SUBMITTED:
            item = _prompt_item_from_event(event)
            open_prompts.append(item)
            prompts_by_event_id[item["prompt_event_id"]] = item
            if item["request_id"]:
                prompts_by_request_id[item["request_id"]] = item
            items.append(item)
        elif event.event_type == RuntimeSessionEventType.ASSISTANT_MESSAGE:
            prompt = _find_prompt_for_response(
                event,
                open_prompts=open_prompts,
                prompts_by_request_id=prompts_by_request_id,
                prompts_by_event_id=prompts_by_event_id,
            )
            if prompt is None:
                items.append(_generic_item_from_event(event))
            else:
                _complete_prompt_item(prompt, event)
        elif event.event_type == RuntimeSessionEventType.CHILD_TASK_STARTED:
            item = _child_task_item_from_started_event(event)
            correlation_key = _child_task_correlation_key_from_item(item)
            if correlation_key:
                child_tasks_by_correlation_key[correlation_key] = item
            items.append(item)
        elif event.event_type == RuntimeSessionEventType.CHILD_TASK_COMPLETED:
            correlation_key = _child_task_correlation_key_from_event(event)
            child_item = child_tasks_by_correlation_key.get(correlation_key) if correlation_key else None
            if child_item is None:
                items.append(_generic_item_from_event(event))
            else:
                _complete_child_task_item(child_item, event)
        else:
            items.append(_generic_item_from_event(event))

    return {
        "summary": summarize_runtime_session(log),
        "items": items,
        "item_count": len(items),
        "in_flight_count": len([item for item in items if _is_in_flight_item(item)]),
        "error_count": len([item for item in items if _is_error_item(item)]),
    }


def read_runtime_session_timeline_by_id(
    store: RuntimeSessionReadStore,
    session_id: str,
) -> RuntimeSessionTimeline | None:
    log = store.load(session_id)
    return build_runtime_session_timeline(log) if log is not None else None


def read_runtime_session_timeline_by_run_id(
    store: RuntimeSessionReadStore,
    run_id: str,
) -> RuntimeSessionTimeline | None:
    return read_runtime_session_timeline_by_id(store, runtime_session_id_for_run(run_id))


def _prompt_item_from_event(event: RuntimeSessionEvent) -> RuntimeSessionTimelineItem:
    return {
        "kind": "prompt",
        "status": "in_flight",
        "sequence_start": event.sequence,
        "sequence_end": None,
        "started_at": event.timestamp,
        "completed_at": None,
        "role": _read_str(event.payload.get("role")),
        "cwd": _read_str(event.payload.get("cwd")),
        "prompt_preview": _preview(event.payload.get("prompt")),
        "response_preview": "",
        "error": "",
        "request_id": _read_str(event.payload.get("requestId")),
        "prompt_event_id": event.event_id,
        "response_event_id": "",
    }


def _find_prompt_for_response(
    event: RuntimeSessionEvent,
    *,
    open_prompts: list[RuntimeSessionTimelineItem],
    prompts_by_request_id: dict[str, RuntimeSessionTimelineItem],
    prompts_by_event_id: dict[str, RuntimeSessionTimelineItem],
) -> RuntimeSessionTimelineItem | None:
    request_id = _read_str(event.payload.get("requestId"))
    prompt_event_id = _read_str(event.payload.get("promptEventId"))
    prompt = (prompts_by_request_id.get(request_id) if request_id else None) or (
        prompts_by_event_id.get(prompt_event_id) if prompt_event_id else None
    )
    if prompt is None and (request_id or prompt_event_id):
        return None
    matched = prompt or (open_prompts[0] if open_prompts else None)
    if matched is None:
        return None

    prompts_by_event_id.pop(matched["prompt_event_id"], None)
    if matched["request_id"]:
        prompts_by_request_id.pop(matched["request_id"], None)
    if matched in open_prompts:
        open_prompts.remove(matched)
    return matched


def _complete_prompt_item(item: RuntimeSessionTimelineItem, event: RuntimeSessionEvent) -> None:
    error = _read_str(event.payload.get("error"))
    is_error = _read_bool(event.payload.get("isError")) or error != ""
    item["status"] = "failed" if is_error else "completed"
    item["sequence_end"] = event.sequence
    item["completed_at"] = event.timestamp
    item["response_preview"] = _preview(event.payload.get("text"))
    item["error"] = error
    item["response_event_id"] = event.event_id
    item["role"] = item["role"] or _read_str(event.payload.get("role"))
    item["cwd"] = item["cwd"] or _read_str(event.payload.get("cwd"))


def _child_task_item_from_started_event(event: RuntimeSessionEvent) -> RuntimeSessionTimelineItem:
    return {
        "kind": "child_task",
        "status": "started",
        "sequence_start": event.sequence,
        "sequence_end": None,
        "started_at": event.timestamp,
        "completed_at": None,
        "task_id": _read_str(event.payload.get("taskId")),
        "child_session_id": _read_str(event.payload.get("childSessionId")),
        "worker_id": _read_str(event.payload.get("workerId")),
        "role": _read_str(event.payload.get("role")),
        "cwd": _read_str(event.payload.get("cwd")),
        "depth": _read_nullable_number(event.payload.get("depth")),
        "max_depth": _read_nullable_number(event.payload.get("maxDepth")),
        "result_preview": "",
        "error": "",
    }


def _child_task_correlation_key_from_item(item: RuntimeSessionTimelineItem) -> str:
    return _child_task_correlation_key(item["task_id"], item["child_session_id"])


def _child_task_correlation_key_from_event(event: RuntimeSessionEvent) -> str:
    return _child_task_correlation_key(
        _read_str(event.payload.get("taskId")),
        _read_str(event.payload.get("childSessionId")),
    )


def _child_task_correlation_key(task_id: str, child_session_id: str) -> str:
    if child_session_id:
        return f"child_session_id:{child_session_id}"
    if task_id:
        return f"task_id:{task_id}"
    return ""


def _complete_child_task_item(item: RuntimeSessionTimelineItem, event: RuntimeSessionEvent) -> None:
    error = _read_str(event.payload.get("error"))
    is_error = _read_bool(event.payload.get("isError")) or error != ""
    item["status"] = "failed" if is_error else "completed"
    item["sequence_end"] = event.sequence
    item["completed_at"] = event.timestamp
    item["result_preview"] = _preview(event.payload.get("result"))
    item["error"] = error
    item["child_session_id"] = item["child_session_id"] or _read_str(event.payload.get("childSessionId"))
    item["worker_id"] = item["worker_id"] or _read_str(event.payload.get("workerId"))
    item["role"] = item["role"] or _read_str(event.payload.get("role"))
    item["cwd"] = item["cwd"] or _read_str(event.payload.get("cwd"))


def _generic_item_from_event(event: RuntimeSessionEvent) -> RuntimeSessionTimelineItem:
    details = _event_details(event.payload)
    detail_text = " ".join(f"{key}={value}" for key, value in _event_title_details(details).items())
    return {
        "kind": "event",
        "sequence": event.sequence,
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "timestamp": event.timestamp,
        "title": f"{event.event_type.value}{f' {detail_text}' if detail_text else ''}",
        "details": details,
    }


def _event_title_details(details: dict[str, str | int | float | bool]) -> dict[str, str | int | float | bool]:
    return {
        key: details[key]
        for key in ["command", "tool", "exitCode", "taskId", "childSessionId", "entryId", "entryCount", "components"]
        if key in details
    }


def _event_details(payload: dict[str, Any]) -> dict[str, str | int | float | bool]:
    details: dict[str, str | int | float | bool] = {}
    for key in [
        "role",
        "cwd",
        "command",
        "tool",
        "exitCode",
        "taskId",
        "childSessionId",
        "entryId",
        "entryCount",
        "components",
        "ledgerPath",
        "generation",
    ]:
        value = payload.get(key)
        if isinstance(value, str) and value:
            details[key] = _preview(value)
        elif isinstance(value, int | float) and not isinstance(value, bool):
            details[key] = value
        elif isinstance(value, bool):
            details[key] = value
    return details


def _is_in_flight_item(item: RuntimeSessionTimelineItem) -> bool:
    return bool((item["kind"] == "prompt" and item["status"] == "in_flight") or (
        item["kind"] == "child_task" and item["status"] == "started"
    ))


def _is_error_item(item: RuntimeSessionTimelineItem) -> bool:
    return bool((item["kind"] == "prompt" and item["status"] == "failed") or (
        item["kind"] == "child_task" and item["status"] == "failed"
    ))


def _preview(value: Any, max_length: int = 240) -> str:
    if value is None:
        return ""
    raw = value if isinstance(value, str) else json.dumps(value)
    normalized = " ".join(raw.split())
    return f"{normalized[: max_length - 3]}..." if len(normalized) > max_length else normalized


def _read_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _read_bool(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _read_nullable_number(value: Any) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None
