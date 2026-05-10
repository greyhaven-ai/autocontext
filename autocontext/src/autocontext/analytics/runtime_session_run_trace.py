"""Adapt runtime-session observability logs into canonical RunTrace artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from typing import Any

from autocontext.analytics.run_trace import ActorRef, CausalEdge, ResourceRef, RunTrace, TraceEvent
from autocontext.session.runtime_events import RuntimeSessionEvent, RuntimeSessionEventLog, RuntimeSessionEventType


@dataclass(frozen=True)
class _RuntimeEventRecord:
    event: RuntimeSessionEvent
    log: RuntimeSessionEventLog
    log_index: int


def runtime_session_log_to_run_trace(
    log: RuntimeSessionEventLog,
    *,
    run_id: str | None = None,
    scenario_name: str | None = None,
    child_logs: list[RuntimeSessionEventLog] | None = None,
    trace_id: str | None = None,
) -> RunTrace:
    """Build a deterministic RunTrace from selected runtime-session events.

    Runtime-session logs can contain prompts, model outputs, and arbitrary
    handler metadata. This adapter intentionally maps only a small allowlist of
    lineage and artifact-reference fields into the analytics trace.
    """
    records = _flatten_runtime_events(log, child_logs or [])
    resolved_run_id = run_id or _infer_run_id(log)
    resolved_scenario = scenario_name or _infer_scenario_name(log)
    child_start_by_session: dict[str, str] = {}
    events: list[TraceEvent] = []
    causal_edges: list[CausalEdge] = []
    previous_event_id: str | None = None

    for sequence_number, record in enumerate(records, start=1):
        event_id = f"runtime-{record.event.event_id}"
        if record.event.event_type == RuntimeSessionEventType.CHILD_TASK_STARTED:
            child_session_id = _read_str(record.event.payload.get("childSessionId"))
            if child_session_id:
                child_start_by_session[child_session_id] = event_id

        lineage_parent_id = ""
        if record.log.parent_session_id:
            lineage_parent_id = child_start_by_session.get(record.log.session_id, "")
        parent_event_id = lineage_parent_id or previous_event_id
        cause_event_ids = [parent_event_id] if parent_event_id else []
        trace_event = TraceEvent(
            event_id=event_id,
            run_id=resolved_run_id,
            generation_index=_generation_index(record.event),
            sequence_number=sequence_number,
            timestamp=record.event.timestamp,
            category=_category_for(record.event),
            event_type=_trace_event_type(record.event),
            actor=_actor_for(record),
            resources=_resources_for(record.event),
            summary=_summary_for(record.event),
            detail=_detail_for(record),
            parent_event_id=parent_event_id,
            cause_event_ids=cause_event_ids,
            evidence_ids=[],
            severity=_severity_for(record.event),
            stage=_stage_for(record),
            outcome=_outcome_for(record.event),
            duration_ms=None,
            metadata={
                "scenario": resolved_scenario,
                "source": "runtime_session",
                "runtime_session_id": record.log.session_id,
            },
        )
        events.append(trace_event)
        if parent_event_id:
            causal_edges.append(
                CausalEdge(
                    source_event_id=parent_event_id,
                    target_event_id=event_id,
                    relation="triggers",
                )
            )
        previous_event_id = event_id

    created_at = events[0].timestamp if events else log.created_at
    return RunTrace(
        trace_id=trace_id or f"trace-{resolved_run_id}-runtime-session",
        run_id=resolved_run_id,
        generation_index=None,
        schema_version="1.0.0",
        events=events,
        causal_edges=causal_edges,
        created_at=created_at,
        metadata={
            "scenario": resolved_scenario,
            "source": "runtime_session",
            "source_session_id": log.session_id,
            "child_session_count": len(child_logs or []),
            "event_count": len(events),
        },
    )


def _flatten_runtime_events(
    log: RuntimeSessionEventLog,
    child_logs: list[RuntimeSessionEventLog],
) -> list[_RuntimeEventRecord]:
    records: list[_RuntimeEventRecord] = []
    for log_index, current_log in enumerate([log, *child_logs]):
        records.extend(
            _RuntimeEventRecord(event=event, log=current_log, log_index=log_index)
            for event in current_log.events
        )
    return sorted(
        records,
        key=lambda record: (
            record.event.timestamp,
            record.log_index,
            record.event.sequence,
            record.event.event_id,
        ),
    )


def _trace_event_type(event: RuntimeSessionEvent) -> str:
    return f"runtime_{event.event_type.value}"


def _actor_for(record: _RuntimeEventRecord) -> ActorRef:
    event = record.event
    payload = event.payload
    if event.event_type == RuntimeSessionEventType.SHELL_COMMAND:
        command_name = _read_str(payload.get("commandName")) or _read_str(payload.get("command")) or "command"
        return ActorRef(actor_type="tool", actor_id=command_name, actor_name=command_name)
    if event.event_type == RuntimeSessionEventType.TOOL_CALL:
        tool_name = _read_str(payload.get("toolName")) or _read_str(payload.get("tool")) or "tool"
        return ActorRef(actor_type="tool", actor_id=tool_name, actor_name=tool_name)
    if event.event_type in {RuntimeSessionEventType.CHILD_TASK_STARTED, RuntimeSessionEventType.CHILD_TASK_COMPLETED}:
        return ActorRef(actor_type="system", actor_id="runtime_session", actor_name="runtime_session")
    if event.event_type == RuntimeSessionEventType.COMPACTION:
        return ActorRef(actor_type="system", actor_id="compaction_ledger", actor_name="compaction_ledger")
    role = _read_str(payload.get("role")) or _read_str(record.log.metadata.get("role")) or "runtime"
    return ActorRef(actor_type="role", actor_id=role, actor_name=role)


def _detail_for(record: _RuntimeEventRecord) -> dict[str, Any]:
    event = record.event
    payload = event.payload
    detail: dict[str, Any] = {
        "runtime_session_id": event.session_id or record.log.session_id,
        "runtime_event_id": event.event_id,
        "runtime_event_type": event.event_type.value,
        "sequence": event.sequence,
        "parent_session_id": event.parent_session_id or record.log.parent_session_id,
        "task_id": event.task_id or record.log.task_id,
        "worker_id": event.worker_id or record.log.worker_id,
    }
    _copy_str(payload, detail, "requestId", "request_id")
    _copy_str(payload, detail, "promptEventId", "prompt_event_id")
    _copy_str(payload, detail, "role", "role")
    _copy_str(payload, detail, "cwd", "cwd")
    _copy_str(payload, detail, "phase", "phase")
    _copy_str(payload, detail, "commandName", "command_name")
    _copy_str(payload, detail, "command", "command_name")
    _copy_str(payload, detail, "toolName", "tool_name")
    _copy_str(payload, detail, "tool", "tool_name")
    _copy_str(payload, detail, "argsSummary", "args_summary")
    _copy_str(payload, detail, "taskId", "task_id")
    _copy_str(payload, detail, "childSessionId", "child_session_id")
    _copy_str(payload, detail, "workerId", "worker_id")
    _copy_str(payload, detail, "entryId", "entry_id")
    _copy_str(payload, detail, "components", "components")
    _copy_str(payload, detail, "ledgerPath", "ledger_path")
    _copy_str(payload, detail, "latestEntryPath", "latest_entry_path")
    _copy_str(payload, detail, "firstKeptEntryId", "first_kept_entry_id")
    _copy_str(payload, detail, "promotedKnowledgeId", "promoted_knowledge_id")
    _copy_str(payload, detail, "runId", "run_id")
    _copy_number(payload, detail, "exitCode", "exit_code")
    _copy_number(payload, detail, "depth", "depth")
    _copy_number(payload, detail, "maxDepth", "max_depth")
    _copy_number(payload, detail, "entryCount", "entry_count")
    _copy_number(payload, detail, "generation", "generation")
    _copy_number(payload, detail, "tokensBefore", "tokens_before")
    _copy_bool(payload, detail, "isError", "is_error")
    _copy_str_list(payload, detail, "entryIds", "entry_ids")
    return _json_safe_record(detail)


def _resources_for(event: RuntimeSessionEvent) -> list[ResourceRef]:
    if event.event_type != RuntimeSessionEventType.COMPACTION:
        return []
    ledger_path = _read_str(event.payload.get("ledgerPath"))
    entry_id = _read_str(event.payload.get("entryId")) or "compaction"
    if not ledger_path:
        return []
    return [
        ResourceRef(
            resource_type="artifact",
            resource_id=entry_id,
            resource_name="compaction_ledger",
            resource_path=ledger_path,
        )
    ]


def _category_for(event: RuntimeSessionEvent) -> str:
    if event.event_type in {RuntimeSessionEventType.SHELL_COMMAND, RuntimeSessionEventType.TOOL_CALL}:
        return "tool_invocation"
    if event.event_type == RuntimeSessionEventType.ASSISTANT_MESSAGE and _is_error(event):
        return "failure"
    if event.event_type == RuntimeSessionEventType.COMPACTION:
        return "checkpoint"
    return "action" if event.event_type != RuntimeSessionEventType.ASSISTANT_MESSAGE else "observation"


def _stage_for(record: _RuntimeEventRecord) -> str:
    if record.event.event_type == RuntimeSessionEventType.COMPACTION:
        return "curate"
    role = _read_str(record.event.payload.get("role")) or _read_str(record.log.metadata.get("role"))
    return {
        "competitor": "compete",
        "analyst": "analyze",
        "coach": "coach",
        "architect": "architect",
        "curator": "curate",
    }.get(role, "init")


def _severity_for(event: RuntimeSessionEvent) -> str:
    if _is_error(event):
        return "error"
    exit_code = event.payload.get("exitCode")
    finite_exit_code = _finite_number(exit_code)
    if finite_exit_code is not None and finite_exit_code != 0:
        return "error"
    return "info"


def _outcome_for(event: RuntimeSessionEvent) -> str | None:
    if _is_error(event):
        return "error"
    phase = _read_str(event.payload.get("phase"))
    if phase:
        return phase
    if event.event_type == RuntimeSessionEventType.CHILD_TASK_STARTED:
        return "started"
    if event.event_type == RuntimeSessionEventType.CHILD_TASK_COMPLETED:
        return "completed"
    return None


def _summary_for(event: RuntimeSessionEvent) -> str:
    if event.event_type == RuntimeSessionEventType.SHELL_COMMAND:
        name = _read_str(event.payload.get("commandName")) or _read_str(event.payload.get("command")) or "command"
        return f"Runtime shell command {name}"
    if event.event_type == RuntimeSessionEventType.TOOL_CALL:
        name = _read_str(event.payload.get("toolName")) or _read_str(event.payload.get("tool")) or "tool"
        return f"Runtime tool call {name}"
    if event.event_type == RuntimeSessionEventType.COMPACTION:
        entry_id = _read_str(event.payload.get("entryId"))
        return f"Runtime compaction {entry_id}".strip()
    return _trace_event_type(event).replace("_", " ")


def _generation_index(event: RuntimeSessionEvent) -> int:
    value = _finite_number(event.payload.get("generation"))
    return int(value) if value is not None else 0


def _is_error(event: RuntimeSessionEvent) -> bool:
    return event.payload.get("isError") is True or _read_str(event.payload.get("phase")) == "error"


def _infer_run_id(log: RuntimeSessionEventLog) -> str:
    metadata_run_id = _read_str(log.metadata.get("runId"))
    if metadata_run_id:
        return metadata_run_id
    for event in log.events:
        event_run_id = _read_str(event.payload.get("runId"))
        if event_run_id:
            return event_run_id
    prefix = "run:"
    suffix = ":runtime"
    if log.session_id.startswith(prefix) and log.session_id.endswith(suffix):
        return log.session_id[len(prefix):-len(suffix)]
    return log.session_id


def _infer_scenario_name(log: RuntimeSessionEventLog) -> str:
    return _read_str(log.metadata.get("scenarioName")) or _read_str(log.metadata.get("scenario")) or "runtime_session"


def _copy_str(source: dict[str, Any], target: dict[str, Any], source_key: str, target_key: str) -> None:
    value = _read_str(source.get(source_key))
    if value and target_key not in target:
        target[target_key] = value


def _copy_number(source: dict[str, Any], target: dict[str, Any], source_key: str, target_key: str) -> None:
    value = _finite_number(source.get(source_key))
    if value is not None:
        target[target_key] = value


def _copy_bool(source: dict[str, Any], target: dict[str, Any], source_key: str, target_key: str) -> None:
    value = source.get(source_key)
    if isinstance(value, bool):
        target[target_key] = value


def _copy_str_list(source: dict[str, Any], target: dict[str, Any], source_key: str, target_key: str) -> None:
    value = source.get(source_key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        target[target_key] = list(value)


def _json_safe_record(value: dict[str, Any]) -> dict[str, Any]:
    parsed = json.loads(json.dumps(value, default=str))
    return parsed if isinstance(parsed, dict) else {}


def _read_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return value
    return None
