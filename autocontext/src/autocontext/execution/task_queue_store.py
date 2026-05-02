"""Task queue storage contract for background workers.

The open-source worker uses :class:`SQLiteStore`, but the task runner only
needs this narrow surface. Hosted deployments can provide a stronger storage
adapter, for example a Postgres-backed queue with leases, while preserving the
same runner behavior.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

TaskQueueRow = dict[str, Any]


@runtime_checkable
class TaskQueueStore(Protocol):
    """Minimal store surface required by ``TaskRunner``."""

    def dequeue_task(self) -> TaskQueueRow | None:
        """Atomically claim and return the next runnable task."""

    def get_task(self, task_id: str) -> TaskQueueRow | None:
        """Return a task row by id."""

    def complete_task(
        self,
        task_id: str,
        best_score: float,
        best_output: str,
        total_rounds: int,
        met_threshold: bool,
        result_json: str | None = None,
    ) -> None:
        """Persist a successful task result."""

    def fail_task(self, task_id: str, error: str) -> None:
        """Persist a task failure."""

    def get_calibration_examples(self, scenario_name: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return recent human-feedback anchors for judge calibration."""


@runtime_checkable
class TaskQueueEnqueueStore(TaskQueueStore, Protocol):
    """Queue store surface required by ``enqueue_task``."""

    def enqueue_task(
        self,
        task_id: str,
        spec_name: str,
        priority: int = 0,
        config: dict[str, Any] | None = None,
        scheduled_at: str | None = None,
    ) -> None:
        """Insert a pending task into the queue."""
