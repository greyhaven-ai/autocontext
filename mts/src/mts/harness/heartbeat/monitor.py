"""HeartbeatMonitor — monitors agent heartbeats and detects stalls with escalation."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from mts.harness.audit.types import AuditCategory, AuditEntry
from mts.harness.audit.writer import AppendOnlyAuditWriter
from mts.harness.heartbeat.types import AgentStatus, HeartbeatRecord, StallEvent, StallPolicy


class HeartbeatMonitor:
    """Monitors agent heartbeats and detects stalls with escalation."""

    def __init__(
        self,
        policy: StallPolicy,
        audit_writer: AppendOnlyAuditWriter | None = None,
        on_stall: Callable[[StallEvent], None] | None = None,
    ) -> None:
        self._policy = policy
        self._audit_writer = audit_writer
        self._on_stall = on_stall
        self._heartbeats: dict[str, HeartbeatRecord] = {}
        self._escalation_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def heartbeat(
        self,
        agent_id: str,
        role: str,
        generation: int | None = None,
        detail: str = "",
    ) -> None:
        """Convenience method: create an ACTIVE HeartbeatRecord and record it."""
        record = HeartbeatRecord(
            agent_id=agent_id,
            role=role,
            timestamp=HeartbeatRecord.now(),
            generation=generation,
            status=AgentStatus.ACTIVE,
            detail=detail,
        )
        self.record_heartbeat(record)

    def record_heartbeat(self, record: HeartbeatRecord) -> None:
        """Store a heartbeat record. Resets escalation count if status is ACTIVE."""
        with self._lock:
            self._heartbeats[record.agent_id] = record
            if record.status == AgentStatus.ACTIVE:
                self._escalation_counts[record.agent_id] = 0

    def check_stalls(self) -> list[StallEvent]:
        """Check all tracked agents for stalls and return any stall events."""
        now = datetime.now(UTC)
        events: list[StallEvent] = []
        with self._lock:
            for agent_id, record in list(self._heartbeats.items()):
                if record.status not in (AgentStatus.ACTIVE, AgentStatus.IDLE):
                    continue
                ts = datetime.fromisoformat(record.timestamp)
                elapsed = (now - ts).total_seconds()
                if elapsed <= self._policy.stall_timeout_seconds:
                    continue
                # Stall detected
                esc_index = self._escalation_counts.get(agent_id, 0)
                max_index = len(self._policy.escalation_levels) - 1
                capped_index = min(esc_index, max_index)
                level = self._policy.escalation_levels[capped_index]
                self._escalation_counts[agent_id] = esc_index + 1
                event = StallEvent(
                    agent_id=agent_id,
                    role=record.role,
                    timestamp=now.isoformat(),
                    seconds_since_heartbeat=elapsed,
                    escalation_level=level,
                    action_taken=level.value,
                )
                events.append(event)
                if self._audit_writer is not None:
                    entry = AuditEntry(
                        timestamp=event.timestamp,
                        category=AuditCategory.ERROR,
                        actor=f"heartbeat_monitor:{agent_id}",
                        action=f"stall_detected:{level.value}",
                        detail=f"Agent {agent_id} ({record.role}) stalled for {elapsed:.1f}s",
                        metadata=event.to_dict(),
                    )
                    self._audit_writer.append(entry)
                if self._on_stall is not None:
                    self._on_stall(event)
        return events

    def status(self, agent_id: str) -> HeartbeatRecord | None:
        """Return the latest heartbeat for an agent, or None if not tracked."""
        with self._lock:
            return self._heartbeats.get(agent_id)

    def all_agents(self) -> dict[str, HeartbeatRecord]:
        """Return a copy of all tracked heartbeat records."""
        with self._lock:
            return dict(self._heartbeats)

    def remove_agent(self, agent_id: str) -> None:
        """Remove an agent from tracking."""
        with self._lock:
            self._heartbeats.pop(agent_id, None)
            self._escalation_counts.pop(agent_id, None)

    def summary(self) -> str:
        """Return a human-readable summary table of all tracked agents."""
        with self._lock:
            if not self._heartbeats:
                return "No agents tracked."
            lines = ["Agent ID | Role | Status | Last Heartbeat | Escalation"]
            lines.append("-" * 70)
            for agent_id, record in sorted(self._heartbeats.items()):
                esc = self._escalation_counts.get(agent_id, 0)
                lines.append(
                    f"{agent_id} | {record.role} | {record.status.value} | {record.timestamp} | {esc}"
                )
            return "\n".join(lines)
