"""REST API for monitor conditions and alerts (AC-209)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from autocontext.monitor.types import ConditionType, MonitorCondition, make_id

monitor_router = APIRouter(prefix="/api/monitors", tags=["monitors"])


class CreateMonitorBody(BaseModel):
    name: str
    condition_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    scope: str = "global"


@monitor_router.post("/", status_code=201)
def create_monitor(body: CreateMonitorBody, request: Request, response: Response) -> dict[str, Any]:
    """Create a new monitor condition."""
    store = request.app.state.store
    app_settings = getattr(request.app.state, "app_settings", None)
    engine = getattr(request.app.state, "monitor_engine", None)
    condition_id = make_id()
    cond = MonitorCondition(
        id=condition_id,
        name=body.name,
        condition_type=ConditionType(body.condition_type),
        params=body.params,
        scope=body.scope,
    )
    try:
        if engine is not None:
            engine.create_condition(cond)
        else:
            max_conditions = app_settings.monitor_max_conditions if app_settings is not None else 100
            if store.count_monitor_conditions(active_only=True) >= max_conditions:
                raise HTTPException(status_code=409, detail=f"maximum active monitor conditions reached ({max_conditions})")
            if cond.condition_type == ConditionType.HEARTBEAT_LOST and "timeout_seconds" not in cond.params:
                default_timeout = app_settings.monitor_heartbeat_timeout if app_settings is not None else 300.0
                cond.params = {**cond.params, "timeout_seconds": default_timeout}
            store.insert_monitor_condition(cond)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Location"] = f"/api/monitors/{condition_id}"
    row = store.get_monitor_condition(condition_id)
    return row if row else {"id": condition_id, "name": body.name}


@monitor_router.get("/")
def list_monitors(
    request: Request,
    scope: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """List monitor conditions with optional filters."""
    store = request.app.state.store
    result: list[dict[str, Any]] = store.list_monitor_conditions(active_only=active_only, scope=scope)
    return result


@monitor_router.delete("/{condition_id}", status_code=204)
def delete_monitor(condition_id: str, request: Request) -> Response:
    """Deactivate a monitor condition."""
    store = request.app.state.store
    found = store.deactivate_monitor_condition(condition_id)
    if not found:
        raise HTTPException(status_code=404, detail="Monitor condition not found")
    return Response(status_code=204)


@monitor_router.get("/alerts")
def list_alerts(
    request: Request,
    condition_id: str | None = None,
    scope: str | None = None,
    limit: int = 100,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """List monitor alerts with optional filters."""
    store = request.app.state.store
    result: list[dict[str, Any]] = store.list_monitor_alerts(
        condition_id=condition_id,
        scope=scope,
        limit=limit,
        since=since,
    )
    return result


@monitor_router.post("/{condition_id}/wait")
async def wait_for_alert(
    condition_id: str,
    request: Request,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Long-poll wait for a monitor alert to fire."""
    engine = getattr(request.app.state, "monitor_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Monitor engine not available")

    fired = await asyncio.to_thread(engine.wait_for_alert, condition_id, timeout)

    alert_data: dict[str, Any] | None = None
    if fired:
        store = request.app.state.store
        alerts = store.list_monitor_alerts(condition_id=condition_id, limit=1)
        if alerts:
            alert_data = alerts[0]

    return {"fired": fired, "alert": alert_data}
