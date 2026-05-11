"""Coordinator-first execution for multi-worker missions (AC-515).

Domain concepts:
- Worker: entity tracking one delegated unit of work with lineage
- WorkerStatus: lifecycle (pending → running → completed/failed/redirected)
- Coordinator: aggregate root owning plan, workers, fan-out/fan-in, events
- CoordinatorEvent: structured event for observability
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---- Value Objects ----


class WorkerStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REDIRECTED = "redirected"


class CoordinatorEventType(StrEnum):
    COORDINATOR_CREATED = "coordinator_created"
    WORKER_DELEGATED = "worker_delegated"
    WORKER_STARTED = "worker_started"
    WORKER_COMPLETED = "worker_completed"
    WORKER_FAILED = "worker_failed"
    WORKER_REDIRECTED = "worker_redirected"
    FAN_OUT = "fan_out"
    FAN_IN = "fan_in"


_ACTIVE_STATUSES = frozenset({WorkerStatus.PENDING, WorkerStatus.RUNNING})
_RETRYABLE_STATUSES = frozenset({WorkerStatus.FAILED, WorkerStatus.REDIRECTED})


class CoordinatorEvent(BaseModel):
    """Immutable event in the coordinator event stream."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    event_type: CoordinatorEventType
    timestamp: str = Field(default_factory=_now)
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


# ---- Entities ----


class Worker(BaseModel):
    """Entity tracking one delegated unit of work.

    Workers have lineage: a retried worker references its parent.
    """

    worker_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    task: str
    role: str
    status: WorkerStatus = WorkerStatus.PENDING
    result: str = ""
    error: str = ""
    redirect_reason: str = ""
    parent_worker_id: str = ""
    created_at: str = Field(default_factory=_now)
    completed_at: str = ""

    @classmethod
    def create(
        cls,
        task: str,
        role: str,
        parent_worker_id: str = "",
    ) -> Worker:
        return cls(task=task, role=role, parent_worker_id=parent_worker_id)

    def start(self) -> None:
        self._require_status({WorkerStatus.PENDING}, action="start worker")
        self.status = WorkerStatus.RUNNING

    def complete(self, result: str) -> None:
        self._require_status({WorkerStatus.RUNNING}, action="complete worker")
        self.status = WorkerStatus.COMPLETED
        self.result = result
        self.completed_at = _now()

    def fail(self, error: str = "") -> None:
        self._require_status({WorkerStatus.RUNNING}, action="fail worker")
        self.status = WorkerStatus.FAILED
        self.error = error
        self.completed_at = _now()

    def redirect(self, new_task: str = "", reason: str = "") -> None:
        self._require_status({WorkerStatus.RUNNING}, action="redirect worker")
        self.status = WorkerStatus.REDIRECTED
        self.redirect_reason = reason
        self.completed_at = _now()

    @property
    def is_active(self) -> bool:
        return self.status in _ACTIVE_STATUSES

    def _require_status(
        self,
        allowed: set[WorkerStatus] | frozenset[WorkerStatus],
        action: str,
    ) -> None:
        if self.status not in allowed:
            msg = f"Cannot {action} from status={self.status}"
            raise ValueError(msg)


# ---- Aggregate Root ----


class Coordinator(BaseModel):
    """Aggregate root: owns plan, workers, and fan-out/fan-in.

    Create via Coordinator.create().
    """

    coordinator_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str
    goal: str
    workers: list[Worker] = Field(default_factory=list)
    events: list[CoordinatorEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)

    @classmethod
    def create(cls, session_id: str, goal: str) -> Coordinator:
        coord = cls(session_id=session_id, goal=goal)
        coord._emit(CoordinatorEventType.COORDINATOR_CREATED, {"goal": goal})
        return coord

    # -- Worker management --

    def delegate(self, task: str, role: str, parent_worker_id: str = "") -> Worker:
        """Create and register a new worker."""
        worker = Worker.create(task=task, role=role, parent_worker_id=parent_worker_id)
        self.workers.append(worker)
        self._emit(CoordinatorEventType.WORKER_DELEGATED, {
            "worker_id": worker.worker_id,
            "task": task,
            "role": role,
        })
        return worker

    def fan_out(self, tasks: list[dict[str, str]]) -> list[Worker]:
        """Delegate multiple independent tasks at once."""
        workers = [self.delegate(**t) for t in tasks]
        self._emit(CoordinatorEventType.FAN_OUT, {
            "worker_ids": [w.worker_id for w in workers],
            "count": len(workers),
        })
        return workers

    def fan_in(self) -> list[str]:
        """Collect results from all completed workers."""
        results = [w.result for w in self.workers if w.status == WorkerStatus.COMPLETED]
        self._emit(CoordinatorEventType.FAN_IN, {
            "result_count": len(results),
        })
        return results

    def complete_worker(
        self,
        worker_id: str,
        result: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Mark a worker as completed with its result."""
        worker = self._get_worker(worker_id)
        worker.complete(result=result)
        self._emit(CoordinatorEventType.WORKER_COMPLETED, _worker_event_payload(worker_id, details))

    def start_worker(
        self,
        worker_id: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Mark a delegated worker as running."""
        worker = self._get_worker(worker_id)
        worker.start()
        self._emit(CoordinatorEventType.WORKER_STARTED, _worker_event_payload(worker_id, details))

    def fail_worker(
        self,
        worker_id: str,
        error: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Mark a running worker as failed."""
        worker = self._get_worker(worker_id)
        worker.fail(error=error)
        self._emit(
            CoordinatorEventType.WORKER_FAILED,
            {
                **_worker_event_payload(worker_id, details),
                "error": error,
            },
        )

    def stop_worker(self, worker_id: str, reason: str = "") -> None:
        """Redirect a worker away from its current task."""
        worker = self._get_worker(worker_id)
        worker.redirect(reason=reason)
        self._emit(CoordinatorEventType.WORKER_REDIRECTED, {
            "worker_id": worker_id,
            "reason": reason,
        })

    def retry(self, worker_id: str, new_task: str = "") -> Worker:
        """Create a continuation worker linked to a failed/redirected one."""
        parent = self._get_worker(worker_id)
        if parent.status not in _RETRYABLE_STATUSES:
            msg = (
                "Cannot retry worker unless it is failed or redirected "
                f"(status={parent.status})"
            )
            raise ValueError(msg)
        task = new_task or parent.task
        return self.delegate(task=task, role=parent.role, parent_worker_id=parent.worker_id)

    # -- Queries --

    @property
    def active_workers(self) -> list[Worker]:
        return [w for w in self.workers if w.is_active]

    @property
    def completed_workers(self) -> list[Worker]:
        return [w for w in self.workers if w.status == WorkerStatus.COMPLETED]

    # -- Internal --

    def _get_worker(self, worker_id: str) -> Worker:
        for w in self.workers:
            if w.worker_id == worker_id:
                return w
        msg = f"Worker {worker_id} not found"
        raise KeyError(msg)

    def _emit(self, event_type: CoordinatorEventType, payload: dict[str, Any]) -> None:
        self.events.append(CoordinatorEvent(
            event_type=event_type,
            payload={"coordinator_id": self.coordinator_id, **payload},
        ))


def _worker_event_payload(
    worker_id: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {**dict(details or {}), "worker_id": worker_id}
