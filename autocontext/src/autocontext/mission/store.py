"""AC-697 mission Python parity SQLite store (slice 1).

Mirrors ``ts/src/mission/store.ts`` + ``store-schema-workflow.ts`` +
``store-mappers.ts`` + ``store-lifecycle-workflow.ts``. Persists
missions, steps, subgoals, and verification records to SQLite using
the same table layout the TS runtime uses, so both runtimes can
read the same on-disk database when ``AUTOCONTEXT_DB_PATH`` is
shared.

Subsequent slices add the higher-level mission manager, verifiers,
control-plane workflows, and the typer CLI surface that calls into
this store.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from autocontext.mission.types import (
    MISSION_STATUSES,
    STEP_STATUSES,
    SUBGOAL_STATUSES,
    BudgetUsage,
    Mission,
    MissionBudget,
    MissionStatus,
    MissionStep,
    MissionSubgoal,
    MissionVerificationRecord,
    StepStatus,
    SubgoalStatus,
    VerifierResult,
)

__all__ = ["MissionStore"]


def _require_status(value: str, allowed: frozenset[str], kind: str) -> None:
    """PR #1014 review (P2): ``Literal`` is a static-analysis hint
    only; the store must guard updates at runtime so a typo cannot
    persist an unreadable row that later raises ``ValidationError``
    on read. Mirrors the TS Zod ``StatusSchema.parse(status)``
    pre-write guard."""
    if value not in allowed:
        raise ValueError(f"invalid {kind} status {value!r}; expected one of {sorted(allowed)}")


_MISSION_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "canceled"})
_STEP_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "blocked", "skipped"})
_SUBGOAL_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "skipped"})


def _generate_record_id(prefix: str) -> str:
    """Same shape as TS `generateMissionRecordId`: ``<prefix>-<8 hex>``."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _utc_now_iso() -> str:
    """ISO-8601 timestamp matching TS `new Date().toISOString()`."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _completion_timestamp(status: str, terminal: frozenset[str]) -> str | None:
    return _utc_now_iso() if status in terminal else None


class MissionStore:
    """SQLite-backed mission store mirroring the TS ``MissionStore``.

    Same table layout, same JSON encoding for the budget / metadata
    blobs, same `prefix-<short uuid>` id shape so cross-runtime
    reads work against a shared database file.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db = sqlite3.connect(db_path, isolation_level=None)
        # Match the TS pragma settings.
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        # Identical DDL to ts/src/mission/store-schema-workflow.ts so
        # both runtimes share the on-disk schema. Each statement runs
        # under its own execute() call so sqlite3 can prepare them
        # individually.
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                budget TEXT,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS mission_steps (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL REFERENCES missions(id),
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS mission_verifications (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL REFERENCES missions(id),
                passed INTEGER NOT NULL,
                reason TEXT NOT NULL,
                suggestions TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS mission_subgoals (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL REFERENCES missions(id),
                description TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT
            );
            """
        )

    # -------------------------------------------------------------------
    # Mission CRUD
    # -------------------------------------------------------------------

    def create_mission(
        self,
        *,
        name: str,
        goal: str,
        budget: MissionBudget | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        record_id = _generate_record_id("mission")
        # The TS store stores budget under camelCase keys
        # (`maxSteps`, `maxCostUsd`, `maxDurationMinutes`); mirror that
        # serialisation so cross-runtime reads agree.
        budget_json = json.dumps(self._budget_to_camel(budget)) if budget else None
        self._db.execute(
            "INSERT INTO missions (id, name, goal, budget, metadata) VALUES (?, ?, ?, ?, ?)",
            (record_id, name, goal, budget_json, json.dumps(metadata or {})),
        )
        return record_id

    def get_mission(self, mission_id: str) -> Mission | None:
        row = self._db.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
        if row is None:
            return None
        return self._mission_from_row(row)

    def list_missions(self, status: MissionStatus | None = None) -> list[Mission]:
        if status is not None:
            rows = self._db.execute(
                "SELECT * FROM missions WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM missions ORDER BY created_at DESC").fetchall()
        return [self._mission_from_row(row) for row in rows]

    def update_mission_status(self, mission_id: str, status: MissionStatus) -> None:
        _require_status(status, MISSION_STATUSES, "mission")
        completed_at = _completion_timestamp(status, _MISSION_TERMINAL_STATUSES)
        self._db.execute(
            "UPDATE missions SET status = ?, updated_at = datetime('now'), completed_at = ? WHERE id = ?",
            (status, completed_at, mission_id),
        )

    # -------------------------------------------------------------------
    # Steps
    # -------------------------------------------------------------------

    def add_step(self, mission_id: str, *, description: str) -> str:
        """Append a completed step. Mirrors the TS default of `completed`
        because mission steps are recorded after the agent finished
        them."""
        record_id = _generate_record_id("step")
        self._db.execute(
            "INSERT INTO mission_steps (id, mission_id, description, status) VALUES (?, ?, ?, 'completed')",
            (record_id, mission_id, description),
        )
        return record_id

    def get_steps(self, mission_id: str) -> list[MissionStep]:
        rows = self._db.execute(
            "SELECT * FROM mission_steps WHERE mission_id = ? ORDER BY created_at",
            (mission_id,),
        ).fetchall()
        return [self._step_from_row(row) for row in rows]

    def update_step_status(self, step_id: str, status: StepStatus, result: str | None = None) -> None:
        _require_status(status, STEP_STATUSES, "step")
        completed_at = _completion_timestamp(status, _STEP_TERMINAL_STATUSES)
        self._db.execute(
            "UPDATE mission_steps SET status = ?, result = COALESCE(?, result), completed_at = ? WHERE id = ?",
            (status, result, completed_at, step_id),
        )

    # -------------------------------------------------------------------
    # Verifications
    # -------------------------------------------------------------------

    def record_verification(self, mission_id: str, result: VerifierResult) -> None:
        record_id = _generate_record_id("verify")
        self._db.execute(
            "INSERT INTO mission_verifications (id, mission_id, passed, reason, suggestions, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (
                record_id,
                mission_id,
                1 if result.passed else 0,
                result.reason,
                json.dumps(list(result.suggestions)),
                json.dumps(result.metadata),
            ),
        )

    def get_verifications(self, mission_id: str) -> list[MissionVerificationRecord]:
        rows = self._db.execute(
            "SELECT * FROM mission_verifications WHERE mission_id = ? ORDER BY created_at",
            (mission_id,),
        ).fetchall()
        return [self._verification_from_row(row) for row in rows]

    # -------------------------------------------------------------------
    # Subgoals
    # -------------------------------------------------------------------

    def add_subgoal(self, mission_id: str, *, description: str, priority: int = 1) -> str:
        record_id = _generate_record_id("subgoal")
        self._db.execute(
            "INSERT INTO mission_subgoals (id, mission_id, description, priority) VALUES (?, ?, ?, ?)",
            (record_id, mission_id, description, priority),
        )
        return record_id

    def get_subgoals(self, mission_id: str) -> list[MissionSubgoal]:
        rows = self._db.execute(
            "SELECT * FROM mission_subgoals WHERE mission_id = ? ORDER BY priority ASC, created_at ASC",
            (mission_id,),
        ).fetchall()
        return [self._subgoal_from_row(row) for row in rows]

    def update_subgoal_status(self, subgoal_id: str, status: SubgoalStatus) -> None:
        _require_status(status, SUBGOAL_STATUSES, "subgoal")
        completed_at = _completion_timestamp(status, _SUBGOAL_TERMINAL_STATUSES)
        self._db.execute(
            "UPDATE mission_subgoals SET status = ?, completed_at = ? WHERE id = ?",
            (status, completed_at, subgoal_id),
        )

    # -------------------------------------------------------------------
    # Budget usage
    # -------------------------------------------------------------------

    def get_budget_usage(self, mission_id: str) -> BudgetUsage:
        mission = self.get_mission(mission_id)
        row = self._db.execute(
            "SELECT COUNT(*) AS count FROM mission_steps WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
        steps_used = int(row["count"]) if row is not None else 0
        max_steps = mission.budget.max_steps if mission and mission.budget else None
        max_cost_usd = mission.budget.max_cost_usd if mission and mission.budget else None
        exhausted = max_steps is not None and steps_used >= max_steps
        return BudgetUsage(
            steps_used=steps_used,
            max_steps=max_steps,
            max_cost_usd=max_cost_usd,
            exhausted=exhausted,
        )

    # -------------------------------------------------------------------
    # Bookkeeping
    # -------------------------------------------------------------------

    def get_db_path(self) -> str:
        return self._db_path

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> MissionStore:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # -------------------------------------------------------------------
    # Row mapping (mirrors TS store-mappers.ts)
    # -------------------------------------------------------------------

    @staticmethod
    def _budget_to_camel(budget: MissionBudget) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if budget.max_steps is not None:
            payload["maxSteps"] = budget.max_steps
        if budget.max_cost_usd is not None:
            payload["maxCostUsd"] = budget.max_cost_usd
        if budget.max_duration_minutes is not None:
            payload["maxDurationMinutes"] = budget.max_duration_minutes
        return payload

    @staticmethod
    def _budget_from_camel(payload: dict[str, Any] | None) -> MissionBudget | None:
        if not payload:
            return None
        return MissionBudget(
            max_steps=payload.get("maxSteps"),
            max_cost_usd=payload.get("maxCostUsd"),
            max_duration_minutes=payload.get("maxDurationMinutes"),
        )

    def _mission_from_row(self, row: sqlite3.Row) -> Mission:
        budget_blob = row["budget"]
        budget = self._budget_from_camel(json.loads(budget_blob)) if budget_blob is not None else None
        metadata_blob = row["metadata"] or "{}"
        return Mission(
            id=row["id"],
            name=row["name"],
            goal=row["goal"],
            status=row["status"],
            budget=budget,
            metadata=json.loads(metadata_blob),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> MissionStep:
        status: StepStatus = (
            row["status"] if row["status"] in ("pending", "running", "completed", "failed", "skipped", "blocked") else "pending"
        )
        return MissionStep(
            id=row["id"],
            mission_id=row["mission_id"],
            description=row["description"],
            status=status,
            result=row["result"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _subgoal_from_row(row: sqlite3.Row) -> MissionSubgoal:
        status: SubgoalStatus = (
            row["status"] if row["status"] in ("pending", "active", "completed", "failed", "skipped") else "pending"
        )
        return MissionSubgoal(
            id=row["id"],
            mission_id=row["mission_id"],
            description=row["description"],
            priority=int(row["priority"]),
            status=status,
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _verification_from_row(row: sqlite3.Row) -> MissionVerificationRecord:
        return MissionVerificationRecord(
            id=row["id"],
            passed=bool(row["passed"]),
            reason=row["reason"],
            suggestions=tuple(json.loads(row["suggestions"] or "[]")),
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"],
        )
