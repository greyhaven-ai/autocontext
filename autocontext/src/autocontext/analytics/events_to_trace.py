"""Convert raw NDJSON event streams into canonical RunTrace artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.analytics.run_trace import ActorRef, CausalEdge, ResourceRef, RunTrace, TraceEvent

_CATEGORY_BY_EVENT: dict[str, str] = {
    "run_started": "checkpoint",
    "generation_started": "checkpoint",
    "generation_completed": "checkpoint",
    "generation_timing": "checkpoint",
    "startup_verification": "checkpoint",
    "agents_started": "action",
    "role_event": "action",
    "role_completed": "action",
    "curator_started": "action",
    "curator_completed": "action",
    "skeptic_started": "action",
    "skeptic_completed": "action",
    "tournament_started": "validation",
    "tournament_completed": "validation",
    "match_completed": "validation",
    "holdout_evaluated": "validation",
    "staged_validation_started": "validation",
    "staged_validation_completed": "validation",
    "gate_decided": "validation",
    "analyst_feedback_rated": "observation",
    "consultation_triggered": "observation",
    "consultation_completed": "observation",
    "generation_failed": "failure",
    "generation_budget_exhausted": "failure",
    "validity_check_failed": "failure",
    "harness_validation_failed": "failure",
    "regression_fixtures_failed": "failure",
    "dry_run_failed": "failure",
    "validity_check_passed": "validation",
    "harness_validation_passed": "validation",
    "regression_fixtures_passed": "validation",
    "dry_run_passed": "validation",
}

_STAGE_BY_EVENT: dict[str, str] = {
    "run_started": "init",
    "startup_verification": "init",
    "agents_started": "init",
    "generation_started": "init",
    "generation_completed": "gate",
    "generation_timing": "gate",
    "generation_failed": "gate",
    "generation_budget_exhausted": "gate",
    "tournament_started": "match",
    "tournament_completed": "match",
    "match_completed": "match",
    "holdout_evaluated": "match",
    "staged_validation_started": "gate",
    "staged_validation_completed": "gate",
    "validity_check_failed": "gate",
    "validity_check_passed": "gate",
    "harness_validation_failed": "gate",
    "harness_validation_passed": "gate",
    "regression_fixtures_failed": "gate",
    "regression_fixtures_passed": "gate",
    "dry_run_failed": "gate",
    "dry_run_passed": "gate",
    "gate_decided": "gate",
    "analyst_feedback_rated": "analyze",
    "consultation_triggered": "analyze",
    "consultation_completed": "analyze",
    "curator_started": "curate",
    "curator_completed": "curate",
    "skeptic_started": "analyze",
    "skeptic_completed": "analyze",
}

_ROLE_STAGE: dict[str, str] = {
    "competitor": "compete",
    "analyst": "analyze",
    "coach": "coach",
    "architect": "architect",
    "curator": "curate",
    "skeptic": "analyze",
}


def events_to_trace(events_path: Path, run_id: str) -> RunTrace:
    """Build a RunTrace from an EventStreamEmitter NDJSON file."""
    rows = _read_event_rows(events_path, run_id)
    events: list[TraceEvent] = []
    causal_edges: list[CausalEdge] = []
    previous_event_id: str | None = None
    scenario = ""

    for index, row in enumerate(rows, start=1):
        payload = _payload(row)
        event_type = str(row.get("event") or "unknown")
        if not scenario and isinstance(payload.get("scenario"), str):
            scenario = payload["scenario"]
        event_id = f"{event_type}-{_int_value(row.get('seq'), index)}"
        event = TraceEvent(
            event_id=event_id,
            run_id=run_id,
            generation_index=_generation_index(payload),
            sequence_number=_int_value(row.get("seq"), index),
            timestamp=str(row.get("ts") or ""),
            category=_category_for(event_type),
            event_type=event_type,
            actor=_actor_for(event_type, payload),
            resources=_resources_for(payload),
            summary=_summary_for(event_type, payload),
            detail=payload,
            parent_event_id=previous_event_id,
            cause_event_ids=[previous_event_id] if previous_event_id else [],
            evidence_ids=[],
            severity=_severity_for(event_type, payload),
            stage=_stage_for(event_type, payload),
            outcome=_outcome_for(event_type, payload),
            duration_ms=_duration_ms(payload),
            metadata={"channel": str(row.get("channel") or ""), "scenario": scenario},
        )
        events.append(event)
        if previous_event_id is not None:
            causal_edges.append(CausalEdge(
                source_event_id=previous_event_id,
                target_event_id=event_id,
                relation="triggers",
            ))
        previous_event_id = event_id

    return RunTrace(
        trace_id=f"trace-{run_id}",
        run_id=run_id,
        generation_index=None,
        schema_version="1.0.0",
        events=events,
        causal_edges=causal_edges,
        created_at=events[0].timestamp if events else "",
        metadata={
            "scenario": scenario,
            "source": str(events_path),
            "event_count": len(events),
        },
    )


def collect_run_ids(events_path: Path) -> list[str]:
    """Return run ids present in an EventStreamEmitter NDJSON file."""
    run_ids: set[str] = set()
    for row in _iter_event_rows(events_path):
        payload = _payload(row)
        value = payload.get("run_id")
        if isinstance(value, str) and value:
            run_ids.add(value)
    return sorted(run_ids)


def _read_event_rows(events_path: Path, run_id: str) -> list[dict[str, Any]]:
    return [
        row for row in _iter_event_rows(events_path)
        if _payload(row).get("run_id") == run_id
    ]


def _iter_event_rows(events_path: Path) -> list[dict[str, Any]]:
    if not events_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _category_for(event_type: str) -> str:
    return _CATEGORY_BY_EVENT.get(event_type, "observation")


def _stage_for(event_type: str, payload: dict[str, Any]) -> str:
    role = str(payload.get("role") or "")
    if role in _ROLE_STAGE:
        return _ROLE_STAGE[role]
    return _STAGE_BY_EVENT.get(event_type, "init")


def _actor_for(event_type: str, payload: dict[str, Any]) -> ActorRef:
    role = str(payload.get("role") or "")
    if role:
        return ActorRef(actor_type="role", actor_id=role, actor_name=str(payload.get("subagent_id") or role))
    return ActorRef(actor_type="system", actor_id="event_stream", actor_name=event_type)


def _resources_for(payload: dict[str, Any]) -> list[ResourceRef]:
    model = payload.get("model") or payload.get("model_used")
    if not model:
        return []
    model_text = str(model)
    return [ResourceRef(
        resource_type="model",
        resource_id=model_text,
        resource_name=model_text,
        resource_path="",
    )]


def _summary_for(event_type: str, payload: dict[str, Any]) -> str:
    if isinstance(payload.get("summary"), str):
        return str(payload["summary"])
    if isinstance(payload.get("reason"), str):
        return f"{event_type}: {payload['reason']}"
    return event_type.replace("_", " ")


def _outcome_for(event_type: str, payload: dict[str, Any]) -> str | None:
    for key in ("status", "outcome", "gate_decision", "decision"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    if event_type == "match_completed" and isinstance(payload.get("passed_validation"), bool):
        return "passed" if payload["passed_validation"] else "failed"
    if event_type.endswith("_started"):
        return "started"
    if event_type.endswith("_completed"):
        return "completed"
    if event_type.endswith("_failed"):
        return "failed"
    return None


def _severity_for(event_type: str, payload: dict[str, Any]) -> str:
    explicit = str(payload.get("severity") or "").lower()
    if explicit in {"info", "warning", "error", "critical"}:
        return explicit
    outcome = (_outcome_for(event_type, payload) or "").lower()
    if "critical" in outcome:
        return "critical"
    if event_type.endswith("_failed") or outcome in {"failed", "error", "stalled"}:
        return "error"
    if outcome in {"retry", "rollback", "warning"}:
        return "warning"
    return "info"


def _generation_index(payload: dict[str, Any]) -> int:
    for key in ("generation_index", "generation"):
        value = payload.get(key)
        if value is not None:
            return _int_value(value, 0)
    return 0


def _duration_ms(payload: dict[str, Any]) -> int | None:
    for key in ("duration_ms", "latency_ms"):
        if key in payload:
            return _int_value(payload[key], 0)
    if "duration_seconds" in payload:
        return int(float(payload["duration_seconds"]) * 1000)
    return None


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
