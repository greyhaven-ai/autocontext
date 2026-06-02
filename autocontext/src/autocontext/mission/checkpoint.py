"""AC-697 mission checkpointing (slice 3).

Mirrors ``ts/src/mission/checkpoint.ts`` (AC-411). JSON snapshot of
the full mission state (mission metadata, steps, subgoals,
verifications, budget usage) so a restart can pick up where the
previous process left off.

``save_checkpoint`` writes ``<mission_id>-<unix_ms>.json`` to the
caller-supplied directory and returns the resulting path.
``load_checkpoint`` re-creates a mission row + child rows with the
original ids; the operator can then ``rehydrate_mission_verifier``
to rebind the verifier from the metadata blob (see
``autocontext.mission.verifiers``).
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.mission.store import MissionStore

__all__ = [
    "CHECKPOINT_VERSION",
    "load_checkpoint",
    "save_checkpoint",
]


CHECKPOINT_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _serialise(value: Any) -> Any:
    """``model_dump(mode="json")`` for Pydantic models, plain
    pass-through otherwise. Used to serialise per-row records into
    the checkpoint payload without hand-mapping every field."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _budget_snake_to_camel(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    mapping = {
        "max_steps": "maxSteps",
        "max_cost_usd": "maxCostUsd",
        "max_duration_minutes": "maxDurationMinutes",
    }
    for snake, camel in mapping.items():
        if snake in payload and payload[snake] is not None:
            out[camel] = payload[snake]
    return out


def save_checkpoint(store: MissionStore, mission_id: str, checkpoint_dir: str | Path) -> str:
    """Persist the full mission state to
    ``<checkpoint_dir>/<mission_id>-<unix_ms>.json``.

    Mirrors the TS shape: parent dir is created on demand; the
    returned path is the absolute checkpoint file path.
    """
    target_dir = Path(checkpoint_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    mission = store.get_mission(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")

    steps = store.get_steps(mission_id)
    subgoals = store.get_subgoals(mission_id)
    verifications = store.get_verifications(mission_id)
    budget_usage = store.get_budget_usage(mission_id)

    payload: dict[str, Any] = {
        "version": CHECKPOINT_VERSION,
        "checkpointedAt": _utc_now_iso(),
        "mission": _serialise(mission),
        "steps": [_serialise(s) for s in steps],
        "subgoals": [_serialise(s) for s in subgoals],
        "verifications": [_serialise(v) for v in verifications],
        "budgetUsage": _serialise(budget_usage),
    }

    filename = f"{mission_id}-{int(time.time() * 1000)}.json"
    out_path = target_dir / filename
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(out_path)


def load_checkpoint(store: MissionStore, checkpoint_path: str | Path) -> str:
    """Re-create a mission from its checkpoint JSON. Returns the
    restored mission id (same id as the original)."""
    raw = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
    mission = raw["mission"]
    original_id = str(mission["id"])

    db = store._db  # noqa: SLF001 — checkpoint restore needs raw write access
    cursor = db.execute("SELECT id FROM missions WHERE id = ?", (original_id,))
    if cursor.fetchone() is not None:
        raise ValueError(f"Cannot restore checkpoint: mission {original_id} already exists")

    budget_blob: str | None = None
    if mission.get("budget"):
        # Mission was Pydantic-dumped before serialisation so the
        # budget keys arrive in snake_case. The store persists them in
        # the TS-compatible camelCase shape for cross-runtime DB
        # reads, so we convert here before re-inserting.
        budget_blob = json.dumps(_budget_snake_to_camel(mission["budget"]))
    metadata_blob = json.dumps(mission.get("metadata") or {})

    db.execute(
        "INSERT INTO missions (id, name, goal, status, budget, metadata, created_at, updated_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            original_id,
            mission["name"],
            mission["goal"],
            mission["status"],
            budget_blob,
            metadata_blob,
            mission.get("created_at") or _utc_now_iso(),
            mission.get("updated_at"),
            mission.get("completed_at"),
        ),
    )

    for step in raw.get("steps", []):
        db.execute(
            "INSERT INTO mission_steps (id, mission_id, description, status, result, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                step["id"],
                original_id,
                step["description"],
                step["status"],
                step.get("result"),
                step.get("created_at") or _utc_now_iso(),
                step.get("completed_at"),
            ),
        )

    for subgoal in raw.get("subgoals", []):
        db.execute(
            "INSERT INTO mission_subgoals (id, mission_id, description, priority, status, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                subgoal["id"],
                original_id,
                subgoal["description"],
                subgoal["priority"],
                subgoal["status"],
                subgoal.get("created_at") or _utc_now_iso(),
                subgoal.get("completed_at"),
            ),
        )

    for verification in raw.get("verifications", []):
        record_id = verification.get("id")
        if not isinstance(record_id, str) or not record_id:
            record_id = f"verify-restored-{uuid.uuid4().hex[:8]}"
        db.execute(
            "INSERT INTO mission_verifications "
            "(id, mission_id, passed, reason, suggestions, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                original_id,
                1 if verification["passed"] else 0,
                verification["reason"],
                json.dumps(verification.get("suggestions") or []),
                json.dumps(verification.get("metadata") or {}),
                verification.get("created_at") or _utc_now_iso(),
            ),
        )

    return original_id
