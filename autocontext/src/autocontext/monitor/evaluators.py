"""Per-type evaluator functions for monitor conditions (AC-209).

Each evaluator is a pure function returning ``MonitorAlert | None``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.monitor.types import MonitorAlert, MonitorCondition, make_id


def _scope_matches(payload: dict[str, Any], scope: str) -> bool:
    """Return whether the payload matches the condition scope."""
    if scope == "global":
        return True
    if scope.startswith("run:"):
        return str(payload.get("run_id", "")) == scope[4:]
    return False


def evaluate_metric_threshold(
    event: str,
    payload: dict[str, Any],
    condition: MonitorCondition,
) -> MonitorAlert | None:
    """Fire when a payload metric crosses a threshold.

    Params:
        metric: key in payload to read
        threshold: numeric threshold
        direction: "above" or "below"
    """
    if not _scope_matches(payload, condition.scope):
        return None

    metric_key = condition.params.get("metric", "")
    threshold = float(condition.params.get("threshold", 0))
    direction = condition.params.get("direction", "above")

    value = payload.get(metric_key)
    if value is None:
        return None

    value = float(value)
    fired = (direction == "above" and value >= threshold) or (direction == "below" and value <= threshold)
    if not fired:
        return None

    return MonitorAlert(
        id=make_id(),
        condition_id=condition.id,
        condition_name=condition.name,
        condition_type=condition.condition_type,
        scope=condition.scope,
        detail=f"{metric_key}={value} {direction} threshold {threshold}",
        fired_at=datetime.now(UTC).isoformat(),
        payload={"metric": metric_key, "value": value, "threshold": threshold, "direction": direction},
    )


def evaluate_stall_window(
    event: str,
    payload: dict[str, Any],
    condition: MonitorCondition,
    gate_history: list[str],
) -> MonitorAlert | None:
    """Fire when consecutive non-advance gate decisions >= window.

    Params:
        window: int, number of consecutive non-advance decisions to trigger
    """
    if not _scope_matches(payload, condition.scope):
        return None

    window = int(condition.params.get("window", 3))

    if len(gate_history) < window:
        return None

    # Count consecutive non-advance from the tail
    consecutive = 0
    for decision in reversed(gate_history):
        if decision == "advance":
            break
        consecutive += 1

    if consecutive < window:
        return None

    return MonitorAlert(
        id=make_id(),
        condition_id=condition.id,
        condition_name=condition.name,
        condition_type=condition.condition_type,
        scope=condition.scope,
        detail=f"{consecutive} consecutive non-advance decisions (window={window})",
        fired_at=datetime.now(UTC).isoformat(),
        payload={"consecutive": consecutive, "window": window, "tail": gate_history[-window:]},
    )


def evaluate_artifact_created(
    event: str,
    payload: dict[str, Any],
    condition: MonitorCondition,
) -> MonitorAlert | None:
    """Fire when a file appears at the specified path.

    Params:
        path: filesystem path to check
    """
    if not _scope_matches(payload, condition.scope):
        return None

    target = condition.params.get("path", "")
    if not target or not Path(target).exists():
        return None

    return MonitorAlert(
        id=make_id(),
        condition_id=condition.id,
        condition_name=condition.name,
        condition_type=condition.condition_type,
        scope=condition.scope,
        detail=f"Artifact found at {target}",
        fired_at=datetime.now(UTC).isoformat(),
        payload={"path": target},
    )


def evaluate_process_exit(
    event: str,
    payload: dict[str, Any],
    condition: MonitorCondition,
) -> MonitorAlert | None:
    """Fire on run_completed / process_exit events matching the condition scope.

    The scope format is ``run:<run_id>`` — we match against payload ``run_id``.
    """
    if event not in ("run_completed", "process_exit"):
        return None

    if not _scope_matches(payload, condition.scope):
        return None

    return MonitorAlert(
        id=make_id(),
        condition_id=condition.id,
        condition_name=condition.name,
        condition_type=condition.condition_type,
        scope=condition.scope,
        detail=f"Process exit: event={event}",
        fired_at=datetime.now(UTC).isoformat(),
        payload=dict(payload),
    )


def evaluate_heartbeat_lost(
    condition: MonitorCondition,
    last_event_time: float,
    now: float,
    *,
    default_timeout_seconds: float = 300.0,
) -> MonitorAlert | None:
    """Fire when no event has been received for longer than timeout_seconds.

    Params:
        timeout_seconds: float, seconds of silence before firing
    """
    timeout = float(condition.params.get("timeout_seconds", default_timeout_seconds))
    elapsed = now - last_event_time

    if elapsed <= timeout:
        return None

    return MonitorAlert(
        id=make_id(),
        condition_id=condition.id,
        condition_name=condition.name,
        condition_type=condition.condition_type,
        scope=condition.scope,
        detail=f"No events for {elapsed:.1f}s (timeout={timeout:.1f}s)",
        fired_at=datetime.now(UTC).isoformat(),
        payload={"elapsed": elapsed, "timeout": timeout},
    )
