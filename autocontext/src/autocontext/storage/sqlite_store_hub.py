from __future__ import annotations

import json
import sqlite3
from typing import Any


class SQLiteHubStoreMixin:
    def connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    @staticmethod
    def _parse_json_field(raw: Any, default: Any) -> Any:
        raise NotImplementedError

    # ---- Research Hub metadata (AC-267) ----

    def _parse_hub_session_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = self._parse_json_field(result.pop("metadata_json", "{}"), {})
        result["shared"] = bool(result.get("shared", 0))
        return result

    def upsert_hub_session(
        self,
        session_id: str,
        *,
        owner: str | None = None,
        status: str | None = None,
        lease_expires_at: str | None = None,
        last_heartbeat_at: str | None = None,
        shared: bool | None = None,
        external_link: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        existing = self.get_hub_session(session_id)
        merged_owner = owner if owner is not None else (str(existing["owner"]) if existing is not None else "")
        merged_status = status if status is not None else (str(existing["status"]) if existing is not None else "active")
        merged_lease = (
            lease_expires_at
            if lease_expires_at is not None
            else (str(existing["lease_expires_at"]) if existing is not None else "")
        )
        merged_heartbeat = (
            last_heartbeat_at
            if last_heartbeat_at is not None
            else (str(existing["last_heartbeat_at"]) if existing is not None else "")
        )
        merged_shared = shared if shared is not None else (bool(existing["shared"]) if existing is not None else False)
        merged_external_link = (
            external_link if external_link is not None else (str(existing["external_link"]) if existing is not None else "")
        )
        merged_metadata = metadata if metadata is not None else (dict(existing["metadata"]) if existing is not None else {})

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO hub_sessions(
                    session_id, owner, status, lease_expires_at, last_heartbeat_at,
                    shared, external_link, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    owner = excluded.owner,
                    status = excluded.status,
                    lease_expires_at = excluded.lease_expires_at,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    shared = excluded.shared,
                    external_link = excluded.external_link,
                    metadata_json = excluded.metadata_json,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    session_id,
                    merged_owner,
                    merged_status,
                    merged_lease,
                    merged_heartbeat,
                    1 if merged_shared else 0,
                    merged_external_link,
                    json.dumps(merged_metadata),
                ),
            )

    def get_hub_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hub_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return self._parse_hub_session_row(dict(row))

    def list_hub_sessions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM hub_sessions ORDER BY updated_at DESC").fetchall()
            return [self._parse_hub_session_row(dict(row)) for row in rows]

    def heartbeat_hub_session(self, session_id: str, *, last_heartbeat_at: str, lease_expires_at: str | None = None) -> None:
        existing = self.get_hub_session(session_id)
        if existing is None:
            self.upsert_hub_session(
                session_id,
                last_heartbeat_at=last_heartbeat_at,
                lease_expires_at=lease_expires_at or "",
            )
            return
        self.upsert_hub_session(
            session_id,
            owner=str(existing["owner"]),
            status=str(existing["status"]),
            lease_expires_at=lease_expires_at if lease_expires_at is not None else str(existing["lease_expires_at"]),
            last_heartbeat_at=last_heartbeat_at,
            shared=bool(existing["shared"]),
            external_link=str(existing["external_link"]),
            metadata=dict(existing["metadata"]),
        )

    def _parse_hub_package_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["tags"] = self._parse_json_field(result.pop("tags_json", "[]"), [])
        result["metadata"] = self._parse_json_field(result.pop("metadata_json", "{}"), {})
        return result

    def save_hub_package_record(
        self,
        *,
        package_id: str,
        scenario_name: str,
        scenario_family: str,
        source_run_id: str,
        source_generation: int,
        title: str,
        description: str,
        promotion_level: str,
        best_score: float,
        best_elo: float,
        payload_path: str,
        strategy_package_path: str,
        tags: list[str],
        metadata: dict[str, Any] | None = None,
        created_at: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO hub_packages(
                    package_id, scenario_name, scenario_family, source_run_id, source_generation,
                    title, description, promotion_level, best_score, best_elo,
                    payload_path, strategy_package_path, tags_json, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(package_id) DO UPDATE SET
                    scenario_name = excluded.scenario_name,
                    scenario_family = excluded.scenario_family,
                    source_run_id = excluded.source_run_id,
                    source_generation = excluded.source_generation,
                    title = excluded.title,
                    description = excluded.description,
                    promotion_level = excluded.promotion_level,
                    best_score = excluded.best_score,
                    best_elo = excluded.best_elo,
                    payload_path = excluded.payload_path,
                    strategy_package_path = excluded.strategy_package_path,
                    tags_json = excluded.tags_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    package_id,
                    scenario_name,
                    scenario_family,
                    source_run_id,
                    source_generation,
                    title,
                    description,
                    promotion_level,
                    best_score,
                    best_elo,
                    payload_path,
                    strategy_package_path,
                    json.dumps(tags),
                    json.dumps(metadata or {}),
                    created_at,
                ),
            )

    def get_hub_package_record(self, package_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hub_packages WHERE package_id = ?",
                (package_id,),
            ).fetchone()
            if row is None:
                return None
            return self._parse_hub_package_row(dict(row))

    def list_hub_package_records(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM hub_packages ORDER BY created_at DESC").fetchall()
            return [self._parse_hub_package_row(dict(row)) for row in rows]

    def _parse_hub_result_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["tags"] = self._parse_json_field(result.pop("tags_json", "[]"), [])
        result["metadata"] = self._parse_json_field(result.pop("metadata_json", "{}"), {})
        return result

    def save_hub_result_record(
        self,
        *,
        result_id: str,
        scenario_name: str,
        run_id: str,
        package_id: str | None,
        title: str,
        best_score: float,
        best_elo: float,
        payload_path: str,
        tags: list[str],
        metadata: dict[str, Any] | None = None,
        created_at: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO hub_results(
                    result_id, scenario_name, run_id, package_id, title,
                    best_score, best_elo, payload_path, tags_json, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(result_id) DO UPDATE SET
                    scenario_name = excluded.scenario_name,
                    run_id = excluded.run_id,
                    package_id = excluded.package_id,
                    title = excluded.title,
                    best_score = excluded.best_score,
                    best_elo = excluded.best_elo,
                    payload_path = excluded.payload_path,
                    tags_json = excluded.tags_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    result_id,
                    scenario_name,
                    run_id,
                    package_id,
                    title,
                    best_score,
                    best_elo,
                    payload_path,
                    json.dumps(tags),
                    json.dumps(metadata or {}),
                    created_at,
                ),
            )

    def get_hub_result_record(self, result_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hub_results WHERE result_id = ?",
                (result_id,),
            ).fetchone()
            if row is None:
                return None
            return self._parse_hub_result_row(dict(row))

    def list_hub_result_records(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM hub_results ORDER BY created_at DESC").fetchall()
            return [self._parse_hub_result_row(dict(row)) for row in rows]

    def _parse_hub_promotion_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = self._parse_json_field(result.pop("metadata_json", "{}"), {})
        return result

    def save_hub_promotion_record(
        self,
        *,
        event_id: str,
        package_id: str,
        source_run_id: str,
        action: str,
        actor: str,
        label: str | None,
        metadata: dict[str, Any] | None = None,
        created_at: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO hub_promotions(
                    event_id, package_id, source_run_id, action, actor, label, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    package_id = excluded.package_id,
                    source_run_id = excluded.source_run_id,
                    action = excluded.action,
                    actor = excluded.actor,
                    label = excluded.label,
                    metadata_json = excluded.metadata_json
                """,
                (
                    event_id,
                    package_id,
                    source_run_id,
                    action,
                    actor,
                    label,
                    json.dumps(metadata or {}),
                    created_at,
                ),
            )

    def get_hub_promotion_record(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hub_promotions WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                return None
            return self._parse_hub_promotion_row(dict(row))

    def list_hub_promotion_records(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM hub_promotions ORDER BY created_at DESC").fetchall()
            return [self._parse_hub_promotion_row(dict(row)) for row in rows]
