"""AC-697 mission checkpointing (slice 3).

Mirrors ``ts/src/mission/checkpoint.ts`` (AC-411). JSON snapshot of
the full mission state (mission metadata, steps, subgoals,
verifications, budget usage) so a restart can pick up where the
previous process left off.

``save_checkpoint`` writes ``<mission_id>-<unix_ns>-<uuid8>.json``
to the caller-supplied directory and returns the resulting path.
The nanosecond timestamp + uuid suffix make the path collision-free
even on fast hardware and across concurrent writers, and the
ns-prefixed name still sorts newest-first lexicographically.
``load_checkpoint`` re-creates a mission row + child rows with the
original ids inside a single SQLite transaction (an insert failure
rolls back the partial restore so the operator can retry without
first cleaning up). Both TS-shaped (camelCase ``createdAt`` /
``budget.maxSteps`` / ...) and Python-shaped (snake_case) inputs
are accepted on restore so a shared ``AUTOCONTEXT_DB_PATH`` can
resume from either runtime's checkpoints. The operator can then
``rehydrate_mission_verifier`` to rebind the verifier from the
metadata blob (see ``autocontext.mission.verifiers``).
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

    # PR #1016 review (P3): two saves landing in the same
    # millisecond overwrote each other because the filename was only
    # ``<mission_id>-<unix_ms>.json``. Use nanosecond resolution +
    # an 8-char uuid suffix so the path is unique even on fast
    # hardware and across concurrent writers. Newest-first ordering
    # by filename still works because the nanosecond prefix sorts
    # lexicographically.
    filename = f"{mission_id}-{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
    out_path = target_dir / filename
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(out_path)


def _pick(payload: dict[str, Any], *keys: str) -> Any:
    """Return the first non-None value among ``keys``. PR #1016 review
    (P2): a TS-shaped checkpoint stores fields in camelCase
    (``createdAt`` / ``maxSteps`` / ...) while a Python-shaped
    checkpoint stores them in snake_case (``created_at`` /
    ``max_steps`` / ...). The loader accepts both so a shared
    ``AUTOCONTEXT_DB_PATH`` resumes from either runtime's
    checkpoints without dropping budget caps or provenance."""
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _budget_to_camel(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalise budget payload to camelCase DB shape, accepting
    either camelCase (TS) or snake_case (Python) keys on input.
    Returns ``None`` if the payload carries no recognised fields."""
    if not payload:
        return None
    mapping = {
        "maxSteps": ("maxSteps", "max_steps"),
        "maxCostUsd": ("maxCostUsd", "max_cost_usd"),
        "maxDurationMinutes": ("maxDurationMinutes", "max_duration_minutes"),
    }
    out: dict[str, Any] = {}
    for camel, candidates in mapping.items():
        value = _pick(payload, *candidates)
        if value is not None:
            out[camel] = value
    return out or None


def load_checkpoint(store: MissionStore, checkpoint_path: str | Path) -> str:
    """Re-create a mission from its checkpoint JSON. Returns the
    restored mission id (same id as the original).

    PR #1016 review (P2 + P2):

    - Accepts both TS-shaped (camelCase ``createdAt`` /
      ``budget.maxSteps`` / ...) and Python-shaped (snake_case)
      checkpoints so a shared ``AUTOCONTEXT_DB_PATH`` can resume
      from either runtime's saves without dropping budget caps or
      timestamps.
    - Restore is wrapped in a single SQLite transaction. A row-level
      failure (duplicate step id, FK violation, etc.) rolls back the
      partial restore so the operator can retry without first having
      to clean up a half-loaded mission row.
    """
    raw = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
    mission = raw["mission"]
    original_id = str(mission["id"])

    db = store._db  # noqa: SLF001 — checkpoint restore needs raw write access
    cursor = db.execute("SELECT id FROM missions WHERE id = ?", (original_id,))
    if cursor.fetchone() is not None:
        raise ValueError(f"Cannot restore checkpoint: mission {original_id} already exists")

    budget_blob: str | None = None
    budget_dict = _budget_to_camel(mission.get("budget"))
    if budget_dict is not None:
        budget_blob = json.dumps(budget_dict)
    metadata_blob = json.dumps(mission.get("metadata") or {})

    # PR #1016 review (P2): wrap the multi-row restore in a single
    # transaction. The store opens its connection with
    # ``isolation_level=None`` (autocommit), so we drive BEGIN /
    # COMMIT / ROLLBACK explicitly. Any insert error during restore
    # rolls back the mission row + every child row inserted so far,
    # which leaves the DB free for a retry.
    db.execute("BEGIN")
    try:
        db.execute(
            "INSERT INTO missions (id, name, goal, status, budget, metadata, "
            "created_at, updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                original_id,
                mission["name"],
                mission["goal"],
                mission["status"],
                budget_blob,
                metadata_blob,
                _pick(mission, "createdAt", "created_at") or _utc_now_iso(),
                _pick(mission, "updatedAt", "updated_at"),
                _pick(mission, "completedAt", "completed_at"),
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
                    _pick(step, "createdAt", "created_at") or _utc_now_iso(),
                    _pick(step, "completedAt", "completed_at"),
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
                    _pick(subgoal, "createdAt", "created_at") or _utc_now_iso(),
                    _pick(subgoal, "completedAt", "completed_at"),
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
                    _pick(verification, "createdAt", "created_at") or _utc_now_iso(),
                ),
            )
    except Exception:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")

    return original_id
