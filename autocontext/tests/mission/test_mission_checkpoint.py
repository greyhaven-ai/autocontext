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
