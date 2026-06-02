"""AC-697 mission Python parity store tests (slice 1).

Mirrors the unit-test surface from ``ts/tests/mission/store.test.ts``
plus the slice-1 contract pinning tests: SQLite tables created on
open; mission CRUD round-trip; step + subgoal + verification CRUD
round-trip; budget usage; status terminal-completion timestamps.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from autocontext.mission import (
    BudgetUsage,
    MissionBudget,
    VerifierResult,
)
from autocontext.mission.store import MissionStore


def _store(tmp_path: Path) -> MissionStore:
    return MissionStore(str(tmp_path / "mission.sqlite3"))


# ---------------------------------------------------------------------------
# schema bootstrap
# ---------------------------------------------------------------------------


def test_open_creates_missing_parent_directory(tmp_path: Path) -> None:
    """PR #1017 review (P1): opening a path under a not-yet-created
    directory used to fail with `OperationalError: unable to open
    database file` from a fresh checkout. The store now creates the
    parent on demand, matching the other SQLite stores in this
    package."""
    nested = tmp_path / "runs" / "subdir"
    db_path = nested / "mission.sqlite3"
    assert not nested.exists()
    store = MissionStore(str(db_path))
    try:
        assert db_path.is_file()
    finally:
        store.close()


def test_open_creates_all_four_tables(tmp_path: Path) -> None:
    """The TS and Python stores share the same on-disk schema so a
    shared `AUTOCONTEXT_DB_PATH` can be read from either runtime."""
    store = _store(tmp_path)
    try:
        rows = store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
        names = {row["name"] for row in rows}
        assert {
            "missions",
            "mission_steps",
            "mission_verifications",
            "mission_subgoals",
        }.issubset(names)
    finally:
        store.close()


def test_open_enables_foreign_keys_and_wal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        fk = store._db.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        journal = store._db.execute("PRAGMA journal_mode").fetchone()[0]
        # wal mode persists across opens on the same db file.
        assert str(journal).lower() == "wal"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# mission CRUD
# ---------------------------------------------------------------------------


def test_create_and_get_mission_round_trips_all_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(
            name="ship login",
            goal="OAuth handshake passes",
            budget=MissionBudget(max_steps=10, max_cost_usd=5.0),
            metadata={"label": "demo"},
        )
        assert mid.startswith("mission-")
        mission = store.get_mission(mid)
        assert mission is not None
        assert mission.name == "ship login"
        assert mission.goal == "OAuth handshake passes"
        assert mission.status == "active"
        assert mission.budget == MissionBudget(max_steps=10, max_cost_usd=5.0)
        assert mission.metadata == {"label": "demo"}
        assert mission.created_at  # set by sqlite default
    finally:
        store.close()


def test_get_mission_returns_none_for_unknown_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert store.get_mission("mission-deadbeef") is None
    finally:
        store.close()


def test_list_missions_returns_all_inserts(tmp_path: Path) -> None:
    """SQLite `datetime('now')` has second resolution, so inserts in
    the same test tick share a `created_at`. We pin the set of ids
    returned rather than the order, because the order tie-break is
    sqlite's internal rowid which is not part of the contract."""
    store = _store(tmp_path)
    try:
        ids = {store.create_mission(name=f"m{i}", goal=f"g{i}") for i in range(3)}
        missions = store.list_missions()
        assert {m.id for m in missions} == ids
    finally:
        store.close()


def test_list_missions_filters_by_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        m1 = store.create_mission(name="a", goal="g")
        store.create_mission(name="b", goal="g")
        store.update_mission_status(m1, "completed")
        active = store.list_missions(status="active")
        assert [m.name for m in active] == ["b"]
        completed = store.list_missions(status="completed")
        assert [m.name for m in completed] == ["a"]
    finally:
        store.close()


def test_terminal_status_sets_completed_at(tmp_path: Path) -> None:
    """Mirrors the TS `buildMissionCompletionTimestamp` shape."""
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        store.update_mission_status(mid, "completed")
        mission = store.get_mission(mid)
        assert mission is not None
        assert mission.completed_at is not None
        assert mission.updated_at is not None
    finally:
        store.close()


def test_non_terminal_status_does_not_set_completed_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        store.update_mission_status(mid, "paused")
        mission = store.get_mission(mid)
        assert mission is not None
        assert mission.completed_at is None
        assert mission.updated_at is not None
    finally:
        store.close()


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


def test_add_step_defaults_to_completed_status(tmp_path: Path) -> None:
    """Matches the TS default: steps are recorded after the agent
    finished them, so the row inserts as `completed`."""
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        sid = store.add_step(mid, description="ran cli")
        steps = store.get_steps(mid)
        assert len(steps) == 1
        assert steps[0].id == sid
        assert steps[0].status == "completed"
        assert steps[0].description == "ran cli"
    finally:
        store.close()


def test_update_step_status_terminal_sets_completed_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        sid = store.add_step(mid, description="ran cli")
        store.update_step_status(sid, "failed", result="exit 1")
        step = store.get_steps(mid)[0]
        assert step.status == "failed"
        assert step.result == "exit 1"
        assert step.completed_at is not None
    finally:
        store.close()


def test_update_step_status_preserves_existing_result_when_omitted(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        sid = store.add_step(mid, description="ran cli")
        store.update_step_status(sid, "failed", result="first")
        store.update_step_status(sid, "running", result=None)
        step = store.get_steps(mid)[0]
        # status update with `None` result keeps the previously
        # recorded result.
        assert step.result == "first"
        assert step.status == "running"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# verifications
# ---------------------------------------------------------------------------


def test_record_and_get_verifications_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        store.record_verification(
            mid,
            VerifierResult(
                passed=True,
                reason="green",
                suggestions=("ship it",),
                metadata={"score": 0.95},
            ),
        )
        records = store.get_verifications(mid)
        assert len(records) == 1
        record = records[0]
        assert record.passed is True
        assert record.reason == "green"
        assert record.suggestions == ("ship it",)
        assert record.metadata == {"score": 0.95}
        assert record.id.startswith("verify-")
    finally:
        store.close()


def test_record_verification_persists_failure_with_suggestions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        store.record_verification(
            mid,
            VerifierResult(
                passed=False,
                reason="tests failed",
                suggestions=("run pytest -x", "check fixtures"),
            ),
        )
        record = store.get_verifications(mid)[0]
        assert record.passed is False
        assert record.suggestions == ("run pytest -x", "check fixtures")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# subgoals
# ---------------------------------------------------------------------------


def test_add_subgoal_and_order_by_priority_then_created_at(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        store.add_subgoal(mid, description="lower", priority=2)
        store.add_subgoal(mid, description="higher", priority=1)
        store.add_subgoal(mid, description="lowest", priority=3)
        subgoals = store.get_subgoals(mid)
        assert [s.priority for s in subgoals] == [1, 2, 3]
        assert [s.description for s in subgoals] == [
            "higher",
            "lower",
            "lowest",
        ]
    finally:
        store.close()


def test_update_subgoal_terminal_sets_completed_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        sgid = store.add_subgoal(mid, description="x")
        store.update_subgoal_status(sgid, "completed")
        subgoal = store.get_subgoals(mid)[0]
        assert subgoal.status == "completed"
        assert subgoal.completed_at is not None
    finally:
        store.close()


# ---------------------------------------------------------------------------
# budget usage
# ---------------------------------------------------------------------------


def test_budget_usage_counts_steps(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g", budget=MissionBudget(max_steps=2))
        store.add_step(mid, description="s1")
        usage = store.get_budget_usage(mid)
        assert usage.steps_used == 1
        assert usage.max_steps == 2
        assert usage.exhausted is False
        store.add_step(mid, description="s2")
        usage = store.get_budget_usage(mid)
        assert usage.steps_used == 2
        assert usage.exhausted is True
    finally:
        store.close()


def test_budget_usage_without_budget_returns_unexhausted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        store.add_step(mid, description="s1")
        usage = store.get_budget_usage(mid)
        assert usage == BudgetUsage(steps_used=1, exhausted=False)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# cross-runtime DB compatibility (camelCase budget keys)
# ---------------------------------------------------------------------------


def test_budget_serialises_with_camel_case_keys_for_ts_compat(
    tmp_path: Path,
) -> None:
    """The TS store persists budget fields under camelCase keys
    (`maxSteps`, `maxCostUsd`, `maxDurationMinutes`). Mirror exactly
    so a shared db file round-trips across runtimes."""
    store = _store(tmp_path)
    try:
        mid = store.create_mission(
            name="x",
            goal="g",
            budget=MissionBudget(max_steps=10, max_cost_usd=1.0, max_duration_minutes=60.0),
        )
        row = store._db.execute("SELECT budget FROM missions WHERE id = ?", (mid,)).fetchone()
        payload = json.loads(row["budget"])
        assert payload == {
            "maxSteps": 10,
            "maxCostUsd": 1.0,
            "maxDurationMinutes": 60.0,
        }
    finally:
        store.close()


def test_budget_camel_keys_round_trip_to_snake_case_model(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        # Insert a row using the camelCase keys the TS runtime writes;
        # the Python loader must surface them as the snake_case model
        # fields without losing data.
        store._db.execute(
            "INSERT INTO missions (id, name, goal, budget) VALUES (?, ?, ?, ?)",
            (
                "mission-fromtso",
                "ts-mission",
                "g",
                json.dumps({"maxSteps": 4, "maxCostUsd": 2.5}),
            ),
        )
        mission = store.get_mission("mission-fromtso")
        assert mission is not None
        assert mission.budget == MissionBudget(max_steps=4, max_cost_usd=2.5)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# bookkeeping
# ---------------------------------------------------------------------------


def test_get_db_path_returns_constructor_arg(tmp_path: Path) -> None:
    path = str(tmp_path / "x.sqlite3")
    store = MissionStore(path)
    try:
        assert store.get_db_path() == path
    finally:
        store.close()


def test_context_manager_closes(tmp_path: Path) -> None:
    path = str(tmp_path / "ctx.sqlite3")
    with MissionStore(path) as store:
        assert store.get_mission("nope") is None
    # connection closed
    with pytest.raises(sqlite3.ProgrammingError):
        store._db.execute("SELECT 1")


# ---------------------------------------------------------------------------
# PR #1014 review (P2): runtime status validation
# ---------------------------------------------------------------------------


def test_update_mission_status_rejects_unknown_value_before_write(
    tmp_path: Path,
) -> None:
    """`Literal` is a static-analysis hint only. Without the runtime
    guard a typo would persist an unreadable row that later raises
    `ValidationError` on read. Mirrors the TS Zod
    `StatusSchema.parse(status)` pre-write guard."""
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        with pytest.raises(ValueError, match="invalid mission status 'banana'"):
            store.update_mission_status(mid, "banana")  # type: ignore[arg-type]
        # Row is still readable; the existing status is unchanged.
        mission = store.get_mission(mid)
        assert mission is not None
        assert mission.status == "active"
    finally:
        store.close()


def test_update_step_status_rejects_unknown_value_before_write(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        sid = store.add_step(mid, description="x")
        with pytest.raises(ValueError, match="invalid step status 'banana'"):
            store.update_step_status(sid, "banana")  # type: ignore[arg-type]
        step = store.get_steps(mid)[0]
        # original status preserved
        assert step.status == "completed"
    finally:
        store.close()


def test_update_subgoal_status_rejects_unknown_value_before_write(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        mid = store.create_mission(name="x", goal="g")
        sgid = store.add_subgoal(mid, description="x")
        with pytest.raises(ValueError, match="invalid subgoal status 'banana'"):
            store.update_subgoal_status(sgid, "banana")  # type: ignore[arg-type]
        subgoal = store.get_subgoals(mid)[0]
        assert subgoal.status == "pending"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# PR #1014 review (P2): primitive coercion blocked via StrictInt / StrictBool
# ---------------------------------------------------------------------------


def test_mission_budget_rejects_bool_for_max_steps() -> None:
    """`MissionBudget(max_steps=True)` used to coerce to 1; strict
    typing now rejects it. Mirrors the TS Zod schema's int-only
    contract."""
    from pydantic import ValidationError

    from autocontext.mission import MissionBudget

    with pytest.raises(ValidationError):
        MissionBudget(max_steps=True)  # type: ignore[arg-type]


def test_mission_budget_rejects_string_for_max_steps() -> None:
    from pydantic import ValidationError

    from autocontext.mission import MissionBudget

    with pytest.raises(ValidationError):
        MissionBudget(max_steps="5")  # type: ignore[arg-type]


def test_mission_budget_rejects_string_for_max_cost_usd() -> None:
    from pydantic import ValidationError

    from autocontext.mission import MissionBudget

    with pytest.raises(ValidationError):
        MissionBudget(max_cost_usd="2.5")  # type: ignore[arg-type]


def test_verifier_result_rejects_string_for_passed() -> None:
    """`VerifierResult(passed="no")` used to coerce to False; strict
    typing now rejects it. Mirrors the TS Zod boolean contract."""
    from pydantic import ValidationError

    from autocontext.mission import VerifierResult

    with pytest.raises(ValidationError):
        VerifierResult(passed="no", reason="x")  # type: ignore[arg-type]


def test_verifier_result_accepts_real_booleans() -> None:
    """The strict guard must not break the happy path."""
    from autocontext.mission import VerifierResult

    ok = VerifierResult(passed=True, reason="ok")
    assert ok.passed is True
    bad = VerifierResult(passed=False, reason="fail")
    assert bad.passed is False
