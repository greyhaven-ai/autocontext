"""Tests for autocontext.harness.heartbeat.types — AgentStatus, EscalationLevel, HeartbeatRecord, StallPolicy, StallEvent."""

from __future__ import annotations

import dataclasses
from datetime import datetime

from autocontext.harness.heartbeat.types import (
    AgentStatus,
    EscalationLevel,
    HeartbeatRecord,
    StallEvent,
    StallPolicy,
)


def test_agent_status_values() -> None:
    assert AgentStatus.ACTIVE.value == "active"
    assert AgentStatus.IDLE.value == "idle"
    assert AgentStatus.STALLED.value == "stalled"
    assert AgentStatus.RECOVERED.value == "recovered"
    assert AgentStatus.TERMINATED.value == "terminated"


def test_escalation_level_values() -> None:
    assert EscalationLevel.WARN.value == "warn"
    assert EscalationLevel.PAUSE.value == "pause"
    assert EscalationLevel.RESTART.value == "restart"
    assert EscalationLevel.TERMINATE.value == "terminate"


def test_heartbeat_record_construction() -> None:
    record = HeartbeatRecord(
        agent_id="agent-1",
        role="competitor",
        timestamp="2025-01-01T00:00:00+00:00",
        generation=3,
        status=AgentStatus.ACTIVE,
        detail="running match",
    )
    assert record.agent_id == "agent-1"
    assert record.role == "competitor"
    assert record.timestamp == "2025-01-01T00:00:00+00:00"
    assert record.generation == 3
    assert record.status == AgentStatus.ACTIVE
    assert record.detail == "running match"


def test_heartbeat_record_frozen() -> None:
    record = HeartbeatRecord(
        agent_id="agent-1",
        role="analyst",
        timestamp="2025-01-01T00:00:00+00:00",
        generation=1,
        status=AgentStatus.IDLE,
    )
    assert dataclasses.is_dataclass(record)
    try:
        record.agent_id = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_heartbeat_record_to_dict_from_dict_roundtrip() -> None:
    record = HeartbeatRecord(
        agent_id="agent-2",
        role="coach",
        timestamp="2025-06-15T12:30:00+00:00",
        generation=5,
        status=AgentStatus.STALLED,
        detail="no response",
    )
    d = record.to_dict()
    assert d == {
        "agent_id": "agent-2",
        "role": "coach",
        "timestamp": "2025-06-15T12:30:00+00:00",
        "generation": 5,
        "status": "stalled",
        "detail": "no response",
    }
    restored = HeartbeatRecord.from_dict(d)
    assert restored == record


def test_heartbeat_record_from_dict_defaults() -> None:
    data = {
        "agent_id": "agent-3",
        "role": "architect",
        "timestamp": "2025-01-01T00:00:00+00:00",
        "status": "recovered",
    }
    record = HeartbeatRecord.from_dict(data)
    assert record.generation is None
    assert record.detail == ""


def test_heartbeat_record_now_returns_iso_timestamp() -> None:
    ts = HeartbeatRecord.now()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None, "Timestamp must be timezone-aware"


def test_stall_policy_defaults() -> None:
    policy = StallPolicy()
    assert policy.stall_timeout_seconds == 300.0
    assert policy.escalation_levels == (
        EscalationLevel.WARN,
        EscalationLevel.PAUSE,
        EscalationLevel.RESTART,
        EscalationLevel.TERMINATE,
    )
    assert policy.escalation_interval_seconds == 60.0
    assert policy.max_restart_attempts == 2


def test_stall_policy_frozen() -> None:
    policy = StallPolicy()
    assert dataclasses.is_dataclass(policy)
    try:
        policy.stall_timeout_seconds = 999.0  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_stall_policy_custom_values() -> None:
    policy = StallPolicy(
        stall_timeout_seconds=120.0,
        escalation_levels=(EscalationLevel.WARN, EscalationLevel.TERMINATE),
        escalation_interval_seconds=30.0,
        max_restart_attempts=5,
    )
    assert policy.stall_timeout_seconds == 120.0
    assert policy.escalation_levels == (EscalationLevel.WARN, EscalationLevel.TERMINATE)
    assert policy.escalation_interval_seconds == 30.0
    assert policy.max_restart_attempts == 5


def test_stall_event_construction_and_to_dict() -> None:
    event = StallEvent(
        agent_id="agent-4",
        role="curator",
        timestamp="2025-03-10T08:00:00+00:00",
        seconds_since_heartbeat=360.5,
        escalation_level=EscalationLevel.RESTART,
        action_taken="restarted agent process",
    )
    assert event.agent_id == "agent-4"
    assert event.role == "curator"
    assert event.seconds_since_heartbeat == 360.5
    assert event.escalation_level == EscalationLevel.RESTART

    d = event.to_dict()
    assert d == {
        "agent_id": "agent-4",
        "role": "curator",
        "timestamp": "2025-03-10T08:00:00+00:00",
        "seconds_since_heartbeat": 360.5,
        "escalation_level": "restart",
        "action_taken": "restarted agent process",
    }
