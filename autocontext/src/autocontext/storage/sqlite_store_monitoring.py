from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autocontext.monitor.types import MonitorAlert, MonitorCondition


class SQLiteMonitorStoreMixin:
    def connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    # ---- Monitor Conditions + Alerts (AC-209) ----

    def insert_monitor_condition(self, condition: MonitorCondition) -> str:
        """Persist a MonitorCondition. Returns the condition id."""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_conditions(id, name, condition_type, params_json, scope, active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    condition.id,
                    condition.name,
                    str(condition.condition_type),
                    json.dumps(condition.params),
                    condition.scope,
                    1 if condition.active else 0,
                ),
            )
            return str(condition.id)

    def list_monitor_conditions(
        self,
        *,
        active_only: bool = True,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """List monitor conditions with optional filters. Returns parsed params."""
        query = "SELECT * FROM monitor_conditions WHERE 1=1"
        params: list[Any] = []
        if active_only:
            query += " AND active = 1"
        if scope is not None:
            query += " AND scope = ?"
            params.append(scope)
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                raw_params = d.pop("params_json", "{}")
                d["params"] = json.loads(raw_params) if isinstance(raw_params, str) else {}
                results.append(d)
            return results

    def count_monitor_conditions(
        self,
        *,
        active_only: bool = True,
        scope: str | None = None,
    ) -> int:
        """Count monitor conditions with optional filters."""
        query = "SELECT COUNT(*) AS cnt FROM monitor_conditions WHERE 1=1"
        params: list[Any] = []
        if active_only:
            query += " AND active = 1"
        if scope is not None:
            query += " AND scope = ?"
            params.append(scope)
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
            return int(row["cnt"]) if row is not None else 0

    def get_monitor_condition(self, condition_id: str) -> dict[str, Any] | None:
        """Get a single monitor condition by id. Returns parsed params."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM monitor_conditions WHERE id = ?",
                (condition_id,),
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            raw_params = d.pop("params_json", "{}")
            d["params"] = json.loads(raw_params) if isinstance(raw_params, str) else {}
            return d

    def deactivate_monitor_condition(self, condition_id: str) -> bool:
        """Deactivate a monitor condition. Returns True if found and updated."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE monitor_conditions SET active = 0 WHERE id = ?",
                (condition_id,),
            )
            row = conn.execute("SELECT changes()").fetchone()
            return bool(row[0] > 0) if row else False

    def insert_monitor_alert(self, alert: MonitorAlert) -> str:
        """Persist a MonitorAlert. Returns the alert id."""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_alerts(id, condition_id, condition_name, condition_type,
                    scope, detail, payload_json, fired_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.id,
                    alert.condition_id,
                    alert.condition_name,
                    str(alert.condition_type),
                    alert.scope,
                    alert.detail,
                    json.dumps(alert.payload),
                    alert.fired_at,
                ),
            )
            return str(alert.id)

    def list_monitor_alerts(
        self,
        *,
        condition_id: str | None = None,
        scope: str | None = None,
        limit: int = 100,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """List monitor alerts with optional filters. Returns parsed payload."""
        query = "SELECT * FROM monitor_alerts WHERE 1=1"
        params: list[Any] = []
        if condition_id is not None:
            query += " AND condition_id = ?"
            params.append(condition_id)
        if scope is not None:
            query += " AND scope = ?"
            params.append(scope)
        if since is not None:
            query += " AND fired_at >= ?"
            params.append(since)
        query += " ORDER BY fired_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                raw_payload = d.pop("payload_json", "{}")
                d["payload"] = json.loads(raw_payload) if isinstance(raw_payload, str) else {}
                results.append(d)
            return results

    def get_latest_monitor_alert(self, condition_id: str) -> dict[str, Any] | None:
        """Return the newest alert for a condition, if one exists."""
        alerts = self.list_monitor_alerts(condition_id=condition_id, limit=1)
        return alerts[0] if alerts else None
