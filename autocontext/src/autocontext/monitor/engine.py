"""Monitor engine — subscribes to events, evaluates conditions, fires alerts (AC-209)."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from autocontext.monitor.evaluators import (
    evaluate_artifact_created,
    evaluate_heartbeat_lost,
    evaluate_metric_threshold,
    evaluate_process_exit,
    evaluate_stall_window,
)
from autocontext.monitor.types import ConditionType, MonitorAlert, MonitorCondition

if TYPE_CHECKING:
    from autocontext.harness.core.events import EventStreamEmitter
    from autocontext.notifications.base import Notifier
    from autocontext.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class MonitorEngine:
    """Evaluates active monitor conditions against incoming events."""

    def __init__(
        self,
        sqlite: SQLiteStore,
        emitter: EventStreamEmitter | None = None,
        notifier: Notifier | None = None,
        *,
        default_heartbeat_timeout: float = 300.0,
        max_conditions: int = 100,
    ) -> None:
        self._sqlite = sqlite
        self._emitter = emitter
        self._notifier = notifier
        self._default_heartbeat_timeout = default_heartbeat_timeout
        self._max_conditions = max_conditions
        self._running = False
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_event_time = time.monotonic()
        self._heartbeat_fired_conditions: set[str] = set()
        # Waiters: condition_id -> list of threading.Event
        self._waiters: dict[str, list[threading.Event]] = {}
        self._waiters_lock = threading.Lock()

    def start(self) -> None:
        """Subscribe to events and start heartbeat daemon."""
        self._running = True
        self._stop_event.clear()
        self._last_event_time = time.monotonic()
        self._heartbeat_fired_conditions.clear()
        if self._emitter is not None:
            self._emitter.subscribe(self._on_event)
        # Start heartbeat daemon thread
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="monitor-heartbeat",
        )
        self._heartbeat_thread.start()

    def stop(self) -> None:
        """Unsubscribe and stop heartbeat thread."""
        self._running = False
        self._stop_event.set()
        if self._emitter is not None:
            try:
                self._emitter.unsubscribe(self._on_event)
            except ValueError:
                logger.debug("monitor.engine: suppressed ValueError", exc_info=True)
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=3.0)
            self._heartbeat_thread = None
        self._heartbeat_fired_conditions.clear()
        # Unblock all waiters
        with self._waiters_lock:
            for events in self._waiters.values():
                for ev in events:
                    ev.set()
            self._waiters.clear()

    def _heartbeat_loop(self) -> None:
        """Background thread that checks for heartbeat-lost conditions."""
        while not self._stop_event.wait(timeout=1.0):
            if not self._running:
                break
            try:
                self._check_heartbeat()
            except Exception:
                logger.debug("heartbeat check error", exc_info=True)

    def _check_heartbeat(self) -> None:
        """Evaluate all active heartbeat_lost conditions."""
        conditions = self._sqlite.list_monitor_conditions(active_only=True)
        now = time.monotonic()
        for row in conditions:
            if row["condition_type"] != ConditionType.HEARTBEAT_LOST:
                continue
            cond = self._row_to_condition(row)
            if cond.id in self._heartbeat_fired_conditions:
                continue
            alert = evaluate_heartbeat_lost(
                cond,
                self._last_event_time,
                now,
                default_timeout_seconds=self._default_heartbeat_timeout,
            )
            if alert is not None:
                self._fire_alert(alert)

    def _on_event(self, event: str, payload: dict[str, object]) -> None:
        """Callback from EventStreamEmitter — evaluate all active conditions."""
        self._last_event_time = time.monotonic()
        self._heartbeat_fired_conditions.clear()
        conditions = self._sqlite.list_monitor_conditions(active_only=True)
        for row in conditions:
            cond = self._row_to_condition(row)
            alert = self._evaluate_condition(event, payload, cond)
            if alert is not None:
                self._fire_alert(alert)

    def create_condition(self, condition: MonitorCondition) -> str:
        """Validate and persist a new condition using engine defaults."""
        active_conditions = self._sqlite.count_monitor_conditions(active_only=True)
        if active_conditions >= self._max_conditions:
            raise ValueError(f"maximum active monitor conditions reached ({self._max_conditions})")
        if condition.condition_type == ConditionType.HEARTBEAT_LOST and "timeout_seconds" not in condition.params:
            condition.params = {**condition.params, "timeout_seconds": self._default_heartbeat_timeout}
        return self._sqlite.insert_monitor_condition(condition)

    def _evaluate_condition(
        self,
        event: str,
        payload: dict[str, object],
        condition: MonitorCondition,
    ) -> MonitorAlert | None:
        """Dispatch to the appropriate evaluator based on condition type."""
        ct = condition.condition_type
        if ct == ConditionType.METRIC_THRESHOLD:
            return evaluate_metric_threshold(event, payload, condition)
        if ct == ConditionType.STALL_WINDOW:
            gate_history = payload.get("gate_history", [])
            if not isinstance(gate_history, list):
                gate_history = []
            return evaluate_stall_window(event, payload, condition, gate_history)
        if ct == ConditionType.ARTIFACT_CREATED:
            return evaluate_artifact_created(event, payload, condition)
        if ct == ConditionType.PROCESS_EXIT:
            return evaluate_process_exit(event, payload, condition)
        # HEARTBEAT_LOST is handled by the background thread, not event-driven
        return None

    def _fire_alert(self, alert: MonitorAlert) -> None:
        """Persist alert, emit event, notify, unblock waiters."""
        try:
            self._sqlite.insert_monitor_alert(alert)
        except Exception:
            logger.warning("failed to persist monitor alert %s", alert.id, exc_info=True)
        if alert.condition_type == ConditionType.HEARTBEAT_LOST:
            self._heartbeat_fired_conditions.add(alert.condition_id)

        # Emit event through the emitter
        if self._emitter is not None:
            try:
                self._emitter.emit(
                    "monitor_alert",
                    {
                        "alert_id": alert.id,
                        "condition_id": alert.condition_id,
                        "condition_name": alert.condition_name,
                        "condition_type": str(alert.condition_type),
                        "scope": alert.scope,
                        "detail": alert.detail,
                    },
                )
            except Exception:
                logger.debug("failed to emit monitor_alert event", exc_info=True)

        # Notify
        if self._notifier is not None:
            try:
                from autocontext.notifications.base import EventType, NotificationEvent

                event = NotificationEvent(
                    type=EventType.THRESHOLD_MET,
                    task_name=f"monitor:{alert.condition_name}",
                    task_id=alert.id,
                    metadata={"condition_id": alert.condition_id, "detail": alert.detail},
                )
                self._notifier.notify(event)
            except Exception:
                logger.debug("notifier error for monitor alert", exc_info=True)

        # Unblock waiters for this condition
        with self._waiters_lock:
            waiters = self._waiters.get(alert.condition_id, [])
            for ev in waiters:
                ev.set()

    def wait_for_alert(self, condition_id: str, timeout: float = 30.0) -> bool:
        """Block until an alert fires for the given condition, or timeout.

        Returns True if an alert fired, False if timed out.
        """
        if self._sqlite.get_latest_monitor_alert(condition_id) is not None:
            return True
        ev = threading.Event()
        with self._waiters_lock:
            self._waiters.setdefault(condition_id, []).append(ev)
        try:
            return ev.wait(timeout=timeout)
        finally:
            with self._waiters_lock:
                waiters = self._waiters.get(condition_id, [])
                if ev in waiters:
                    waiters.remove(ev)

    @staticmethod
    def _row_to_condition(row: dict[str, Any]) -> MonitorCondition:
        """Convert a SQLite row dict to a MonitorCondition dataclass."""
        return MonitorCondition(
            id=row["id"],
            name=row["name"],
            condition_type=ConditionType(row["condition_type"]),
            params=row.get("params", {}),
            scope=row.get("scope", "global"),
            active=bool(row.get("active", 1)),
            created_at=row.get("created_at", ""),
        )


# ---------------------------------------------------------------------------
# Module-level singleton for MCP access
# ---------------------------------------------------------------------------

_engine: MonitorEngine | None = None


def get_engine() -> MonitorEngine:
    """Return the global MonitorEngine instance."""
    if _engine is None:
        raise RuntimeError("MonitorEngine not initialized. Call set_engine() first.")
    return _engine


def set_engine(engine: MonitorEngine) -> None:
    """Set the global MonitorEngine instance."""
    global _engine
    _engine = engine


def clear_engine() -> None:
    """Clear the global MonitorEngine instance."""
    global _engine
    _engine = None
