"""AC-697 mission checkpoint tests (slice 3).

Covers ``save_checkpoint`` round-trip + ``load_checkpoint`` shape
parity with TS, plus the defensive guards (missing mission, restore
into a db that already has the id).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from autocontext.mission import (
    CHECKPOINT_VERSION,
    MissionBudget,
    MissionStore,
    VerifierResult,
    load_checkpoint,
    save_checkpoint,
)


def _store(tmp_path: Path) -> MissionStore:
    return MissionStore(str(tmp_path / "m.sqlite3"))


def test_save_checkpoint_writes_canonical_payload_shape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(
            name="ship login",
            goal="OAuth handshake",
            budget=MissionBudget(max_steps=4),
            metadata={"label": "demo"},
        )
        store.add_step(mid, description="ran cli")
        store.add_subgoal(mid, description="step 1")
        store.record_verification(
            mid,
            VerifierResult(passed=False, reason="not yet"),
        )

        path = save_checkpoint(store, mid, tmp_path / "checkpoints")
        payload = json.loads(Path(path).read_text())
        assert payload["version"] == CHECKPOINT_VERSION
        assert payload["mission"]["id"] == mid
        assert payload["mission"]["name"] == "ship login"
        # Pydantic dump uses snake_case keys for the budget.
        assert payload["mission"]["budget"] == {
            "max_steps": 4,
            "max_cost_usd": None,
            "max_duration_minutes": None,
        }
        assert len(payload["steps"]) == 1
        assert len(payload["subgoals"]) == 1
        assert len(payload["verifications"]) == 1
        assert payload["budgetUsage"]["steps_used"] == 1
    finally:
        store.close()


def test_save_checkpoint_creates_target_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        nested = tmp_path / "a" / "b" / "c"
        path = save_checkpoint(store, mid, nested)
        assert nested.is_dir()
        assert Path(path).is_file()
    finally:
        store.close()


def test_save_checkpoint_missing_mission_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="Mission not found"):
            save_checkpoint(store, "mission-nope", tmp_path / "cp")
    finally:
        store.close()


def test_load_checkpoint_round_trips_mission_with_children(tmp_path: Path) -> None:
    src_store = MissionStore(str(tmp_path / "src.sqlite3"))
    try:
        mid = src_store.create_mission(
            name="x",
            goal="g",
            budget=MissionBudget(max_steps=3, max_cost_usd=1.5),
            metadata={"label": "demo"},
        )
        src_store.add_step(mid, description="s1")
        src_store.add_subgoal(mid, description="sg1", priority=1)
        src_store.record_verification(mid, VerifierResult(passed=True, reason="green"))
        checkpoint_path = save_checkpoint(src_store, mid, tmp_path / "cp")
    finally:
        src_store.close()

    # Fresh store -> load into it.
    dest_path = tmp_path / "dest.sqlite3"
    dest_store = MissionStore(str(dest_path))
    try:
        restored_id = load_checkpoint(dest_store, checkpoint_path)
        assert restored_id == mid

        mission = dest_store.get_mission(restored_id)
        assert mission is not None
        assert mission.name == "x"
        assert mission.budget == MissionBudget(max_steps=3, max_cost_usd=1.5)
        assert dest_store.get_steps(restored_id)[0].description == "s1"
        assert dest_store.get_subgoals(restored_id)[0].priority == 1
        verifications = dest_store.get_verifications(restored_id)
        assert verifications[0].passed is True
    finally:
        dest_store.close()


def test_load_checkpoint_into_db_with_existing_id_rejects(tmp_path: Path) -> None:
    """Restore guards against silently clobbering an existing
    mission row that happens to share the original id."""
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        checkpoint_path = save_checkpoint(store, mid, tmp_path / "cp")
        with pytest.raises(ValueError, match="already exists"):
            load_checkpoint(store, checkpoint_path)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# PR #1016 review (P2): camelCase TS-shaped checkpoint compatibility
# ---------------------------------------------------------------------------


def test_load_checkpoint_accepts_ts_camelcase_budget_and_timestamps(
    tmp_path: Path,
) -> None:
    """PR #1016 review (P2): the TS checkpoint format uses camelCase
    (`createdAt`, `budget.maxSteps`, ...). Before the fix the Python
    loader only read snake_case, so a TS-shaped checkpoint dropped
    the budget caps and regenerated timestamps. The fix accepts
    either shape so a shared `AUTOCONTEXT_DB_PATH` resumes cleanly.
    """
    ts_checkpoint = {
        "version": 1,
        "checkpointedAt": "2026-06-02T00:00:00Z",
        "mission": {
            "id": "mission-tscamel",
            "name": "ts-shaped",
            "goal": "g",
            "status": "active",
            "budget": {"maxSteps": 7, "maxCostUsd": 3.5},
            "metadata": {"missionType": "code"},
            "createdAt": "2026-06-02T01:00:00Z",
            "updatedAt": "2026-06-02T02:00:00Z",
            "completedAt": None,
        },
        "steps": [
            {
                "id": "step-1",
                "mission_id": "mission-tscamel",
                "description": "did a",
                "status": "completed",
                "result": None,
                "createdAt": "2026-06-02T03:00:00Z",
                "completedAt": "2026-06-02T03:01:00Z",
            }
        ],
        "subgoals": [],
        "verifications": [],
        "budgetUsage": {"steps_used": 1, "exhausted": False},
    }
    path = tmp_path / "ts-checkpoint.json"
    path.write_text(json.dumps(ts_checkpoint))

    dest = MissionStore(str(tmp_path / "dest.sqlite3"))
    try:
        restored_id = load_checkpoint(dest, path)
        assert restored_id == "mission-tscamel"
        mission = dest.get_mission(restored_id)
        assert mission is not None
        assert mission.budget == MissionBudget(max_steps=7, max_cost_usd=3.5)
        assert mission.created_at == "2026-06-02T01:00:00Z"
        assert mission.updated_at == "2026-06-02T02:00:00Z"
        # Steps inherited their TS-shaped createdAt too.
        step = dest.get_steps(restored_id)[0]
        assert step.created_at == "2026-06-02T03:00:00Z"
    finally:
        dest.close()


# ---------------------------------------------------------------------------
# PR #1016 review (P2): atomic restore
# ---------------------------------------------------------------------------


def test_load_checkpoint_rolls_back_on_duplicate_child_row(tmp_path: Path) -> None:
    """PR #1016 review (P2): a child-row failure used to leave a
    partial mission + first step behind; a retry then choked on the
    already-existing mission row. The transaction wrapper rolls back
    the partial restore so the operator can retry without first
    cleaning up."""
    duplicate_step_id = "step-dup"
    payload = {
        "version": 1,
        "checkpointedAt": "2026-06-02T00:00:00Z",
        "mission": {
            "id": "mission-rb",
            "name": "x",
            "goal": "g",
            "status": "active",
            "budget": None,
            "metadata": {},
            "created_at": "2026-06-02T00:00:00Z",
            "updated_at": None,
            "completed_at": None,
        },
        "steps": [
            {
                "id": duplicate_step_id,
                "mission_id": "mission-rb",
                "description": "first",
                "status": "completed",
                "result": None,
                "created_at": "2026-06-02T00:01:00Z",
                "completed_at": None,
            },
            # Duplicate id triggers UNIQUE constraint failure on the
            # second insert; the rollback should drop the mission row
            # AND the first step.
            {
                "id": duplicate_step_id,
                "mission_id": "mission-rb",
                "description": "second",
                "status": "completed",
                "result": None,
                "created_at": "2026-06-02T00:02:00Z",
                "completed_at": None,
            },
        ],
        "subgoals": [],
        "verifications": [],
        "budgetUsage": {"steps_used": 2, "exhausted": False},
    }
    path = tmp_path / "rb.json"
    path.write_text(json.dumps(payload))

    dest = MissionStore(str(tmp_path / "dest_rb.sqlite3"))
    try:
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            load_checkpoint(dest, path)
        # Mission row was rolled back.
        assert dest.get_mission("mission-rb") is None
        # First step was rolled back.
        assert (
            dest._db.execute(
                "SELECT COUNT(*) AS c FROM mission_steps WHERE mission_id = ?",
                ("mission-rb",),
            ).fetchone()["c"]
            == 0
        )
        # A retry succeeds after the operator dedupes the steps.
        payload["steps"][1]["id"] = "step-dup-2"  # type: ignore[index]
        path.write_text(json.dumps(payload))
        restored_id = load_checkpoint(dest, path)
        assert restored_id == "mission-rb"
    finally:
        dest.close()


# ---------------------------------------------------------------------------
# PR #1016 review (P3): filename collision
# ---------------------------------------------------------------------------


def test_save_checkpoint_two_saves_in_same_millisecond_do_not_collide(
    tmp_path: Path,
) -> None:
    """PR #1016 review (P3): `<mid>-<unix_ms>.json` collided when two
    saves landed in the same millisecond. The new `<mid>-<ns>-<uuid8>.json`
    layout makes the path unique even on fast hardware and across
    concurrent writers.
    """
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        cp_dir = tmp_path / "cp"
        # Save 10 in a tight loop — millisecond ties are likely.
        paths = {save_checkpoint(store, mid, cp_dir) for _ in range(10)}
        assert len(paths) == 10
        # All files actually exist (no overwrites).
        for p in paths:
            assert Path(p).is_file()
    finally:
        store.close()


def test_load_checkpoint_assigns_id_for_verifications_missing_one(
    tmp_path: Path,
) -> None:
    """Mirror TS guard: a checkpoint produced by an older version
    that didn't persist verification ids still loads, with a fresh
    `verify-restored-<8 hex>` id assigned."""
    src_store = MissionStore(str(tmp_path / "src2.sqlite3"))
    try:
        mid = src_store.create_mission(name="x", goal="g")
        # Build a checkpoint payload with a verification missing an id.
        payload = {
            "version": 1,
            "checkpointedAt": "2026-06-02T00:00:00Z",
            "mission": {
                "id": mid,
                "name": "x",
                "goal": "g",
                "status": "active",
                "budget": None,
                "metadata": {},
                "created_at": f"2026-06-02T00:00:0{int(time.time()) % 9}Z",
                "updated_at": None,
                "completed_at": None,
            },
            "steps": [],
            "subgoals": [],
            "verifications": [
                {
                    "passed": True,
                    "reason": "from-old-format",
                    "suggestions": [],
                    "metadata": {},
                    "created_at": "2026-06-02T00:00:00Z",
                }
            ],
            "budgetUsage": {"steps_used": 0, "exhausted": False},
        }
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(payload))
    finally:
        src_store.close()

    dest = MissionStore(str(tmp_path / "dest2.sqlite3"))
    try:
        restored_id = load_checkpoint(dest, path)
        records = dest.get_verifications(restored_id)
        assert len(records) == 1
        assert records[0].id.startswith("verify-restored-")
        assert records[0].passed is True
    finally:
        dest.close()
