"""Tests for HeartbeatMonitor — stall detection and escalation."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autocontext.harness.audit.writer import AppendOnlyAuditWriter
from autocontext.harness.heartbeat.monitor import HeartbeatMonitor
from autocontext.harness.heartbeat.types import (
    AgentStatus,
    EscalationLevel,
    HeartbeatRecord,
    StallEvent,
    StallPolicy,
)


def _default_policy() -> StallPolicy:
    return StallPolicy(
        stall_timeout_seconds=300.0,
        escalation_levels=(
            EscalationLevel.WARN,
            EscalationLevel.PAUSE,
            EscalationLevel.RESTART,
            EscalationLevel.TERMINATE,
        ),
        escalation_interval_seconds=60.0,
        max_restart_attempts=2,
    )


def _stale_record(agent_id: str, role: str, seconds_ago: float) -> HeartbeatRecord:
    """Create a heartbeat record with a timestamp in the past."""
    ts = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()
    return HeartbeatRecord(
        agent_id=agent_id,
        role=role,
        timestamp=ts,
        generation=1,
        status=AgentStatus.ACTIVE,
    )


def test_record_heartbeat() -> None:
    monitor = HeartbeatMonitor(policy=_default_policy())
    record = HeartbeatRecord(
        agent_id="agent-1",
        role="competitor",
        timestamp=HeartbeatRecord.now(),
        generation=1,
        status=AgentStatus.ACTIVE,
        detail="working",
    )
    monitor.record_heartbeat(record)
    assert monitor.status("agent-1") == record


def test_convenience_heartbeat_method() -> None:
    monitor = HeartbeatMonitor(policy=_default_policy())
    monitor.heartbeat("agent-1", "analyst", generation=2, detail="analyzing")
    rec = monitor.status("agent-1")
    assert rec is not None
    assert rec.agent_id == "agent-1"
    assert rec.role == "analyst"
    assert rec.generation == 2
    assert rec.status == AgentStatus.ACTIVE
    assert rec.detail == "analyzing"


def test_no_stall_when_recent() -> None:
    monitor = HeartbeatMonitor(policy=_default_policy())
    monitor.heartbeat("agent-1", "competitor")
    events = monitor.check_stalls()
    assert events == []


def test_detects_stall() -> None:
    monitor = HeartbeatMonitor(policy=_default_policy())
    # Place a stale heartbeat 600 seconds in the past
    stale = _stale_record("agent-1", "competitor", 600)
    monitor.record_heartbeat(stale)
    events = monitor.check_stalls()
    assert len(events) == 1
    assert events[0].agent_id == "agent-1"
    assert events[0].role == "competitor"
    assert events[0].escalation_level == EscalationLevel.WARN
    assert events[0].action_taken == "warn"
    assert events[0].seconds_since_heartbeat >= 600


def test_escalation_sequence() -> None:
    """Escalation should progress: warn -> pause -> restart -> terminate."""
    monitor = HeartbeatMonitor(policy=_default_policy())
    stale = _stale_record("agent-1", "analyst", 600)
    monitor.record_heartbeat(stale)

    expected = [
        EscalationLevel.WARN,
        EscalationLevel.PAUSE,
        EscalationLevel.RESTART,
        EscalationLevel.TERMINATE,
    ]
    for level in expected:
        # Re-inject stale timestamp (record_heartbeat with ACTIVE would reset escalation)
        monitor._heartbeats["agent-1"] = _stale_record("agent-1", "analyst", 600)
        events = monitor.check_stalls()
        assert len(events) == 1
        assert events[0].escalation_level == level
        assert events[0].action_taken == level.value


def test_escalation_caps_at_max() -> None:
    """Once escalation hits the last level, it stays there."""
    monitor = HeartbeatMonitor(policy=_default_policy())
    stale = _stale_record("agent-1", "coach", 600)
    monitor.record_heartbeat(stale)

    # Run through all 4 levels
    for _ in range(4):
        monitor._heartbeats["agent-1"] = _stale_record("agent-1", "coach", 600)
        monitor.check_stalls()

    # 5th check should still be TERMINATE (capped)
    monitor._heartbeats["agent-1"] = _stale_record("agent-1", "coach", 600)
    events = monitor.check_stalls()
    assert len(events) == 1
    assert events[0].escalation_level == EscalationLevel.TERMINATE


def test_active_heartbeat_resets_escalation() -> None:
    """An ACTIVE heartbeat should reset the escalation counter."""
    monitor = HeartbeatMonitor(policy=_default_policy())
    stale = _stale_record("agent-1", "analyst", 600)
    monitor.record_heartbeat(stale)

    # Escalate twice (warn, pause)
    monitor._heartbeats["agent-1"] = _stale_record("agent-1", "analyst", 600)
    monitor.check_stalls()
    monitor._heartbeats["agent-1"] = _stale_record("agent-1", "analyst", 600)
    monitor.check_stalls()

    # Send a fresh ACTIVE heartbeat — resets escalation
    monitor.heartbeat("agent-1", "analyst")

    # Make it stale again
    monitor._heartbeats["agent-1"] = _stale_record("agent-1", "analyst", 600)
    events = monitor.check_stalls()
    assert len(events) == 1
    assert events[0].escalation_level == EscalationLevel.WARN  # reset to first level


def test_on_stall_callback_called() -> None:
    received: list[StallEvent] = []
    monitor = HeartbeatMonitor(policy=_default_policy(), on_stall=received.append)
    stale = _stale_record("agent-1", "architect", 600)
    monitor.record_heartbeat(stale)
    monitor.check_stalls()
    assert len(received) == 1
    assert received[0].agent_id == "agent-1"
    assert received[0].escalation_level == EscalationLevel.WARN


def test_audit_on_stall(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_path)
    monitor = HeartbeatMonitor(policy=_default_policy(), audit_writer=writer)
    stale = _stale_record("agent-1", "competitor", 600)
    monitor.record_heartbeat(stale)
    monitor.check_stalls()

    entries = writer.read_all()
    assert len(entries) == 1
    assert entries[0].category.value == "error"
    assert "stall_detected:warn" in entries[0].action
    assert "agent-1" in entries[0].detail


def test_status_query() -> None:
    monitor = HeartbeatMonitor(policy=_default_policy())
    assert monitor.status("nonexistent") is None
    monitor.heartbeat("agent-1", "curator")
    rec = monitor.status("agent-1")
    assert rec is not None
    assert rec.role == "curator"


def test_all_agents() -> None:
    monitor = HeartbeatMonitor(policy=_default_policy())
    monitor.heartbeat("agent-1", "competitor")
    monitor.heartbeat("agent-2", "analyst")
    agents = monitor.all_agents()
    assert set(agents.keys()) == {"agent-1", "agent-2"}
    assert agents["agent-1"].role == "competitor"
    assert agents["agent-2"].role == "analyst"


def test_remove_agent() -> None:
    monitor = HeartbeatMonitor(policy=_default_policy())
    monitor.heartbeat("agent-1", "competitor")
    monitor.heartbeat("agent-2", "analyst")
    assert monitor.status("agent-1") is not None
    monitor.remove_agent("agent-1")
    assert monitor.status("agent-1") is None
    assert monitor.status("agent-2") is not None
    # Removing nonexistent agent is safe
    monitor.remove_agent("nonexistent")


def test_summary() -> None:
    monitor = HeartbeatMonitor(policy=_default_policy())
    assert monitor.summary() == "No agents tracked."
    monitor.heartbeat("agent-1", "competitor")
    monitor.heartbeat("agent-2", "analyst")
    s = monitor.summary()
    assert "agent-1" in s
    assert "agent-2" in s
    assert "competitor" in s
    assert "analyst" in s
    assert "active" in s


def test_thread_safety() -> None:
    """Concurrent heartbeats from multiple threads should not raise."""
    monitor = HeartbeatMonitor(policy=_default_policy())
    errors: list[Exception] = []
    barrier = threading.Barrier(10)

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            for j in range(50):
                monitor.heartbeat(f"agent-{idx}", f"role-{idx}", generation=j)
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(worker, i) for i in range(10)]
        for f in futures:
            f.result(timeout=10)

    assert errors == []
    agents = monitor.all_agents()
    assert len(agents) == 10
