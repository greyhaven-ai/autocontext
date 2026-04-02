"""Derived progress digests for operator surfaces (AC-512).

Domain concepts:
- WorkerDigest: compact status of one active worker
- ProgressDigest: aggregate summary derived from coordinator/session state
- Designed for operator re-entry: what's happening, what changed, what's next
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from autocontext.session.coordinator import Coordinator, Worker
    from autocontext.session.types import Session


class WorkerDigest(BaseModel):
    """Compact operator-facing status of one worker."""

    worker_id: str
    role: str
    status: str
    current_action: str
    last_result: str = ""

    @classmethod
    def from_worker(cls, worker: Worker) -> WorkerDigest:
        return cls(
            worker_id=worker.worker_id,
            role=worker.role,
            status=worker.status.value,
            current_action=worker.task[:200],
            last_result=worker.result[:200] if worker.result else "",
        )

    model_config = {"frozen": True}


class ProgressDigest(BaseModel):
    """Aggregate operator-facing summary.

    Derived from coordinator and/or session state — never persisted
    as primary data, always recomputable from source signals.
    """

    goal: str = ""
    summary: str = ""
    active_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    redirected_count: int = 0
    turn_count: int = 0
    worker_digests: list[WorkerDigest] = Field(default_factory=list)
    recent_changes: list[str] = Field(default_factory=list)

    @classmethod
    def from_coordinator(
        cls,
        coordinator: Coordinator,
        max_recent_events: int = 10,
    ) -> ProgressDigest:
        """Build digest from coordinator state and events."""
        from autocontext.session.coordinator import WorkerStatus

        worker_digests = [WorkerDigest.from_worker(w) for w in coordinator.workers]
        active = [w for w in coordinator.workers if w.is_active]
        completed = [w for w in coordinator.workers if w.status == WorkerStatus.COMPLETED]
        failed = [w for w in coordinator.workers if w.status == WorkerStatus.FAILED]
        redirected = [w for w in coordinator.workers if w.status == WorkerStatus.REDIRECTED]

        # Build short summary
        parts: list[str] = []
        if not coordinator.workers:
            parts.append("Idle — no workers delegated yet.")
        else:
            if active:
                tasks = ", ".join(w.task[:50] for w in active[:3])
                parts.append(f"{len(active)} active: {tasks}")
            if completed:
                parts.append(f"{len(completed)} completed")
            if failed:
                parts.append(f"{len(failed)} failed")
            if redirected:
                parts.append(f"{len(redirected)} redirected")
        summary = ". ".join(parts)[:300]

        # Recent changes from event stream
        recent = []
        for event in coordinator.events[-max_recent_events:]:
            label = event.event_type.value.replace("_", " ")
            recent.append(f"{label}: {_compact_payload(event.payload)}")

        return cls(
            goal=coordinator.goal,
            summary=summary,
            active_count=len(active),
            completed_count=len(completed),
            failed_count=len(failed),
            redirected_count=len(redirected),
            worker_digests=worker_digests,
            recent_changes=recent,
        )

    @classmethod
    def from_session(cls, session: Session) -> ProgressDigest:
        """Build digest from a plain session (no coordinator)."""
        return cls(
            goal=session.goal,
            summary=f"Session with {len(session.turns)} turn(s).",
            turn_count=len(session.turns),
        )

    @classmethod
    def empty(cls) -> ProgressDigest:
        """Safe fallback when no signal is available."""
        return cls(summary="No active work.")

    model_config = {"frozen": True}


def _compact_payload(payload: dict[str, Any]) -> str:
    """Render event payload as a short string."""
    parts = []
    for k, v in payload.items():
        if k == "coordinator_id":
            continue
        sv = str(v)[:60]
        parts.append(f"{k}={sv}")
    return ", ".join(parts[:4])
