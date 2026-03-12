"""Tests for AC-209: First-class monitor conditions and wait semantics.

Tests cover:
- ConditionType enum values and MonitorCondition/MonitorAlert construction
- Per-type evaluator functions (metric_threshold, stall_window, artifact_created, process_exit, heartbeat_lost)
- MonitorEngine lifecycle, event-driven alert firing, wait semantics
- SQLite migration + store methods (CRUD for conditions and alerts)
- REST API endpoints (create, list, delete, alerts, wait)
- Integration cycles (metric threshold, stall window, WebSocket)
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.config.settings import AppSettings
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sqlite_store(tmp_path: Path) -> SQLiteStore:
    """Create a SQLiteStore with all migrations applied."""
    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)
    return store


@pytest.fixture()
def emitter(tmp_path: Path) -> Any:
    from autocontext.loop.events import EventStreamEmitter

    return EventStreamEmitter(tmp_path / "events.ndjson")


# ===========================================================================
# 1. Types
# ===========================================================================


class TestMonitorTypes:
    def test_condition_type_enum_values(self) -> None:
        from autocontext.monitor.types import ConditionType

        assert ConditionType.METRIC_THRESHOLD == "metric_threshold"
        assert ConditionType.STALL_WINDOW == "stall_window"
        assert ConditionType.ARTIFACT_CREATED == "artifact_created"
        assert ConditionType.PROCESS_EXIT == "process_exit"
        assert ConditionType.HEARTBEAT_LOST == "heartbeat_lost"

    def test_monitor_condition_construction(self) -> None:
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="abc123",
            name="High score",
            condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.9, "direction": "above"},
            scope="run:test-run",
        )
        assert cond.id == "abc123"
        assert cond.name == "High score"
        assert cond.condition_type == ConditionType.METRIC_THRESHOLD
        assert cond.params["threshold"] == 0.9
        assert cond.scope == "run:test-run"
        assert cond.active is True
        assert cond.created_at == ""

    def test_monitor_alert_construction(self) -> None:
        from autocontext.monitor.types import ConditionType, MonitorAlert

        alert = MonitorAlert(
            id="alert1",
            condition_id="cond1",
            condition_name="High score",
            condition_type=ConditionType.METRIC_THRESHOLD,
            scope="global",
            detail="Score crossed 0.9",
            fired_at="2026-01-01T00:00:00Z",
        )
        assert alert.id == "alert1"
        assert alert.condition_id == "cond1"
        assert alert.payload == {}  # default_factory

    def test_make_id_unique(self) -> None:
        from autocontext.monitor.types import make_id

        ids = {make_id() for _ in range(100)}
        assert len(ids) == 100


# ===========================================================================
# 2. Evaluators
# ===========================================================================


class TestEvaluators:
    def test_metric_threshold_fires_above(self) -> None:
        from autocontext.monitor.evaluators import evaluate_metric_threshold
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        alert = evaluate_metric_threshold("generation_completed", {"best_score": 0.95}, cond)
        assert alert is not None
        assert "0.95" in alert.detail or "0.8" in alert.detail

    def test_metric_threshold_fires_below(self) -> None:
        from autocontext.monitor.evaluators import evaluate_metric_threshold
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="low", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "elo", "threshold": 900.0, "direction": "below"},
            scope="global",
        )
        alert = evaluate_metric_threshold("generation_completed", {"elo": 850.0}, cond)
        assert alert is not None

    def test_metric_threshold_no_fire_below_threshold(self) -> None:
        from autocontext.monitor.evaluators import evaluate_metric_threshold
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        alert = evaluate_metric_threshold("generation_completed", {"best_score": 0.5}, cond)
        assert alert is None

    def test_metric_threshold_wrong_event(self) -> None:
        from autocontext.monitor.evaluators import evaluate_metric_threshold
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        # Metric key not present in payload
        alert = evaluate_metric_threshold("generation_completed", {"mean_score": 0.95}, cond)
        assert alert is None

    def test_metric_threshold_respects_run_scope(self) -> None:
        from autocontext.monitor.evaluators import evaluate_metric_threshold
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="run-high", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="run:target-run",
        )
        alert = evaluate_metric_threshold("generation_completed", {"best_score": 0.95, "run_id": "other-run"}, cond)
        assert alert is None

    def test_stall_window_fires(self) -> None:
        from autocontext.monitor.evaluators import evaluate_stall_window
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c2", name="stall", condition_type=ConditionType.STALL_WINDOW,
            params={"window": 3},
            scope="global",
        )
        gate_history = ["advance", "rollback", "retry", "rollback"]
        alert = evaluate_stall_window("gate_decided", {}, cond, gate_history)
        assert alert is not None

    def test_stall_window_no_fire_short_history(self) -> None:
        from autocontext.monitor.evaluators import evaluate_stall_window
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c2", name="stall", condition_type=ConditionType.STALL_WINDOW,
            params={"window": 3},
            scope="global",
        )
        gate_history = ["rollback", "retry"]
        alert = evaluate_stall_window("gate_decided", {}, cond, gate_history)
        assert alert is None

    def test_stall_window_no_fire_when_advance_breaks_streak(self) -> None:
        from autocontext.monitor.evaluators import evaluate_stall_window
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c2", name="stall", condition_type=ConditionType.STALL_WINDOW,
            params={"window": 3},
            scope="global",
        )
        gate_history = ["rollback", "rollback", "advance", "rollback"]
        alert = evaluate_stall_window("gate_decided", {}, cond, gate_history)
        assert alert is None

    def test_artifact_created_fires(self, tmp_path: Path) -> None:
        from autocontext.monitor.evaluators import evaluate_artifact_created
        from autocontext.monitor.types import ConditionType, MonitorCondition

        target = tmp_path / "output.json"
        target.write_text("{}", encoding="utf-8")
        cond = MonitorCondition(
            id="c3", name="artifact", condition_type=ConditionType.ARTIFACT_CREATED,
            params={"path": str(target)},
            scope="global",
        )
        alert = evaluate_artifact_created("generation_completed", {}, cond)
        assert alert is not None

    def test_artifact_created_no_fire_missing(self) -> None:
        from autocontext.monitor.evaluators import evaluate_artifact_created
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c3", name="artifact", condition_type=ConditionType.ARTIFACT_CREATED,
            params={"path": "/nonexistent/file.json"},
            scope="global",
        )
        alert = evaluate_artifact_created("generation_completed", {}, cond)
        assert alert is None

    def test_process_exit_fires(self) -> None:
        from autocontext.monitor.evaluators import evaluate_process_exit
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c4", name="done", condition_type=ConditionType.PROCESS_EXIT,
            params={},
            scope="run:my-run",
        )
        alert = evaluate_process_exit("run_completed", {"run_id": "my-run"}, cond)
        assert alert is not None

    def test_process_exit_wrong_scope(self) -> None:
        from autocontext.monitor.evaluators import evaluate_process_exit
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c4", name="done", condition_type=ConditionType.PROCESS_EXIT,
            params={},
            scope="run:other-run",
        )
        alert = evaluate_process_exit("run_completed", {"run_id": "my-run"}, cond)
        assert alert is None

    def test_heartbeat_lost_fires_stale(self) -> None:
        from autocontext.monitor.evaluators import evaluate_heartbeat_lost
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c5", name="heartbeat", condition_type=ConditionType.HEARTBEAT_LOST,
            params={"timeout_seconds": 60.0},
            scope="global",
        )
        now = 1000.0
        last = 900.0  # 100s ago > 60s timeout
        alert = evaluate_heartbeat_lost(cond, last, now)
        assert alert is not None

    def test_heartbeat_lost_no_fire_recent(self) -> None:
        from autocontext.monitor.evaluators import evaluate_heartbeat_lost
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c5", name="heartbeat", condition_type=ConditionType.HEARTBEAT_LOST,
            params={"timeout_seconds": 60.0},
            scope="global",
        )
        now = 1000.0
        last = 980.0  # 20s ago < 60s timeout
        alert = evaluate_heartbeat_lost(cond, last, now)
        assert alert is None


# ===========================================================================
# 3. Engine
# ===========================================================================


class TestMonitorEngine:
    def test_engine_start_stop(self, sqlite_store: SQLiteStore, emitter: Any) -> None:
        from autocontext.monitor.engine import MonitorEngine

        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()
        assert engine._running is True
        engine.stop()
        assert engine._running is False

    def test_engine_on_event_fires_alert(self, sqlite_store: SQLiteStore, emitter: Any) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high score", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()
        try:
            engine._on_event("generation_completed", {"best_score": 0.95})
            alerts = sqlite_store.list_monitor_alerts()
            assert len(alerts) >= 1
            assert alerts[0]["condition_id"] == "c1"
        finally:
            engine.stop()

    def test_engine_emits_monitor_alert_event(self, sqlite_store: SQLiteStore, tmp_path: Path) -> None:
        from autocontext.loop.events import EventStreamEmitter
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        emitter = EventStreamEmitter(tmp_path / "events.ndjson")
        captured: list[tuple[str, dict[str, object]]] = []
        emitter.subscribe(lambda e, p: captured.append((e, p)))

        cond = MonitorCondition(
            id="c1", name="high score", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()
        try:
            engine._on_event("generation_completed", {"best_score": 0.95})
            monitor_events = [e for e, _ in captured if e == "monitor_alert"]
            assert len(monitor_events) >= 1
        finally:
            engine.stop()

    def test_engine_notifier_called(self, sqlite_store: SQLiteStore, emitter: Any) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        notifier = MagicMock()
        cond = MonitorCondition(
            id="c1", name="high score", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter, notifier=notifier)
        engine.start()
        try:
            engine._on_event("generation_completed", {"best_score": 0.95})
            assert notifier.notify.call_count >= 1
        finally:
            engine.stop()

    def test_engine_wait_for_alert_true(self, sqlite_store: SQLiteStore, emitter: Any) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high score", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()
        try:
            # Fire alert from another thread after a short delay
            def fire() -> None:
                time.sleep(0.1)
                engine._on_event("generation_completed", {"best_score": 0.95})

            t = threading.Thread(target=fire)
            t.start()
            result = engine.wait_for_alert("c1", timeout=5.0)
            t.join()
            assert result is True
        finally:
            engine.stop()

    def test_engine_wait_for_alert_timeout(self, sqlite_store: SQLiteStore, emitter: Any) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high score", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.99, "direction": "above"},
            scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()
        try:
            result = engine.wait_for_alert("c1", timeout=0.2)
            assert result is False
        finally:
            engine.stop()

    def test_engine_wait_for_existing_alert_returns_immediately(self, sqlite_store: SQLiteStore, emitter: Any) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high score", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()
        try:
            engine._on_event("generation_completed", {"best_score": 0.95})
            result = engine.wait_for_alert("c1", timeout=0.01)
            assert result is True
        finally:
            engine.stop()

    def test_heartbeat_alert_fires_once_per_silence_window(self, sqlite_store: SQLiteStore, emitter: Any) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="hb1", name="heartbeat", condition_type=ConditionType.HEARTBEAT_LOST,
            params={"timeout_seconds": 0.01},
            scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()
        try:
            engine._last_event_time = time.monotonic() - 1.0
            engine._check_heartbeat()
            engine._check_heartbeat()
            alerts = sqlite_store.list_monitor_alerts(condition_id="hb1")
            assert len(alerts) == 1

            engine._on_event("generation_completed", {"run_id": "r1"})
            engine._last_event_time = time.monotonic() - 1.0
            engine._check_heartbeat()
            alerts = sqlite_store.list_monitor_alerts(condition_id="hb1")
            assert len(alerts) == 2
        finally:
            engine.stop()

    def test_engine_deactivated_condition_not_evaluated(self, sqlite_store: SQLiteStore, emitter: Any) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high score", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        sqlite_store.deactivate_monitor_condition("c1")
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()
        try:
            engine._on_event("generation_completed", {"best_score": 0.95})
            alerts = sqlite_store.list_monitor_alerts()
            assert len(alerts) == 0
        finally:
            engine.stop()


# ===========================================================================
# 4. SQLite Storage
# ===========================================================================


class TestMonitorStorage:
    def test_monitor_tables_exist(self, sqlite_store: SQLiteStore) -> None:
        with sqlite_store.connect() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "monitor_conditions" in tables
            assert "monitor_alerts" in tables

    def test_insert_and_get_condition(self, sqlite_store: SQLiteStore) -> None:
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="high score", condition_type=ConditionType.METRIC_THRESHOLD,
            params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
            scope="run:test",
        )
        sqlite_store.insert_monitor_condition(cond)
        row = sqlite_store.get_monitor_condition("c1")
        assert row is not None
        assert row["id"] == "c1"
        assert row["name"] == "high score"
        assert row["condition_type"] == "metric_threshold"
        assert row["params"]["metric"] == "best_score"
        assert row["scope"] == "run:test"
        assert row["active"] == 1

    def test_list_conditions_active_only(self, sqlite_store: SQLiteStore) -> None:
        from autocontext.monitor.types import ConditionType, MonitorCondition

        c1 = MonitorCondition(
            id="c1", name="active", condition_type=ConditionType.METRIC_THRESHOLD,
            params={}, scope="global",
        )
        c2 = MonitorCondition(
            id="c2", name="inactive", condition_type=ConditionType.STALL_WINDOW,
            params={}, scope="global", active=False,
        )
        sqlite_store.insert_monitor_condition(c1)
        sqlite_store.insert_monitor_condition(c2)
        active = sqlite_store.list_monitor_conditions(active_only=True)
        assert len(active) == 1
        assert active[0]["id"] == "c1"

    def test_list_conditions_by_scope(self, sqlite_store: SQLiteStore) -> None:
        from autocontext.monitor.types import ConditionType, MonitorCondition

        c1 = MonitorCondition(
            id="c1", name="g", condition_type=ConditionType.METRIC_THRESHOLD,
            params={}, scope="global",
        )
        c2 = MonitorCondition(
            id="c2", name="r", condition_type=ConditionType.STALL_WINDOW,
            params={}, scope="run:test",
        )
        sqlite_store.insert_monitor_condition(c1)
        sqlite_store.insert_monitor_condition(c2)
        scoped = sqlite_store.list_monitor_conditions(active_only=False, scope="run:test")
        assert len(scoped) == 1
        assert scoped[0]["id"] == "c2"

    def test_deactivate_condition_found(self, sqlite_store: SQLiteStore) -> None:
        from autocontext.monitor.types import ConditionType, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="test", condition_type=ConditionType.METRIC_THRESHOLD,
            params={}, scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        result = sqlite_store.deactivate_monitor_condition("c1")
        assert result is True
        row = sqlite_store.get_monitor_condition("c1")
        assert row is not None
        assert row["active"] == 0

    def test_deactivate_condition_not_found(self, sqlite_store: SQLiteStore) -> None:
        result = sqlite_store.deactivate_monitor_condition("nonexistent")
        assert result is False

    def test_insert_and_list_alerts(self, sqlite_store: SQLiteStore) -> None:
        from autocontext.monitor.types import ConditionType, MonitorAlert, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="test", condition_type=ConditionType.METRIC_THRESHOLD,
            params={}, scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        alert = MonitorAlert(
            id="a1", condition_id="c1", condition_name="test",
            condition_type=ConditionType.METRIC_THRESHOLD,
            scope="global", detail="triggered", fired_at="2026-01-01T00:00:00Z",
            payload={"value": 0.95},
        )
        sqlite_store.insert_monitor_alert(alert)
        alerts = sqlite_store.list_monitor_alerts()
        assert len(alerts) == 1
        assert alerts[0]["id"] == "a1"
        assert alerts[0]["payload"]["value"] == 0.95

    def test_list_alerts_filter_by_condition(self, sqlite_store: SQLiteStore) -> None:
        from autocontext.monitor.types import ConditionType, MonitorAlert, MonitorCondition

        for cid in ("c1", "c2"):
            cond = MonitorCondition(
                id=cid, name=cid, condition_type=ConditionType.METRIC_THRESHOLD,
                params={}, scope="global",
            )
            sqlite_store.insert_monitor_condition(cond)
        a1 = MonitorAlert(
            id="a1", condition_id="c1", condition_name="c1",
            condition_type=ConditionType.METRIC_THRESHOLD,
            scope="global", detail="", fired_at="2026-01-01T00:00:00Z",
        )
        a2 = MonitorAlert(
            id="a2", condition_id="c2", condition_name="c2",
            condition_type=ConditionType.METRIC_THRESHOLD,
            scope="global", detail="", fired_at="2026-01-01T00:00:01Z",
        )
        sqlite_store.insert_monitor_alert(a1)
        sqlite_store.insert_monitor_alert(a2)
        alerts = sqlite_store.list_monitor_alerts(condition_id="c1")
        assert len(alerts) == 1
        assert alerts[0]["condition_id"] == "c1"

    def test_list_alerts_filter_by_scope(self, sqlite_store: SQLiteStore) -> None:
        from autocontext.monitor.types import ConditionType, MonitorAlert, MonitorCondition

        for cid, scope in (("c1", "global"), ("c2", "run:test")):
            cond = MonitorCondition(
                id=cid, name=cid, condition_type=ConditionType.METRIC_THRESHOLD,
                params={}, scope=scope,
            )
            sqlite_store.insert_monitor_condition(cond)
        a1 = MonitorAlert(
            id="a1", condition_id="c1", condition_name="c1",
            condition_type=ConditionType.METRIC_THRESHOLD,
            scope="global", detail="", fired_at="2026-01-01T00:00:00Z",
        )
        a2 = MonitorAlert(
            id="a2", condition_id="c2", condition_name="c2",
            condition_type=ConditionType.METRIC_THRESHOLD,
            scope="run:test", detail="", fired_at="2026-01-01T00:00:01Z",
        )
        sqlite_store.insert_monitor_alert(a1)
        sqlite_store.insert_monitor_alert(a2)
        alerts = sqlite_store.list_monitor_alerts(scope="run:test")
        assert len(alerts) == 1
        assert alerts[0]["scope"] == "run:test"

    def test_list_alerts_since(self, sqlite_store: SQLiteStore) -> None:
        from autocontext.monitor.types import ConditionType, MonitorAlert, MonitorCondition

        cond = MonitorCondition(
            id="c1", name="test", condition_type=ConditionType.METRIC_THRESHOLD,
            params={}, scope="global",
        )
        sqlite_store.insert_monitor_condition(cond)
        a1 = MonitorAlert(
            id="a1", condition_id="c1", condition_name="test",
            condition_type=ConditionType.METRIC_THRESHOLD,
            scope="global", detail="", fired_at="2026-01-01T00:00:00Z",
        )
        a2 = MonitorAlert(
            id="a2", condition_id="c1", condition_name="test",
            condition_type=ConditionType.METRIC_THRESHOLD,
            scope="global", detail="", fired_at="2026-06-01T00:00:00Z",
        )
        sqlite_store.insert_monitor_alert(a1)
        sqlite_store.insert_monitor_alert(a2)
        alerts = sqlite_store.list_monitor_alerts(since="2026-03-01T00:00:00Z")
        assert len(alerts) == 1
        assert alerts[0]["id"] == "a2"


# ===========================================================================
# 5. REST API
# ===========================================================================


@pytest.fixture()
def monitor_app(tmp_path: Path) -> TestClient:
    """Build a minimal FastAPI app with monitor router + mock engine."""
    from autocontext.server.monitor_api import monitor_router

    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)

    app = FastAPI()
    app.state.store = store
    app.state.app_settings = AppSettings()
    app.state.monitor_engine = None  # will set per-test if needed
    app.include_router(monitor_router)
    return TestClient(app)


class TestMonitorRestAPI:
    def test_create_monitor_201(self, monitor_app: TestClient) -> None:
        resp = monitor_app.post("/api/monitors", json={
            "name": "High score",
            "condition_type": "metric_threshold",
            "params": {"metric": "best_score", "threshold": 0.8, "direction": "above"},
            "scope": "global",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["name"] == "High score"
        assert "Location" in resp.headers or "location" in resp.headers

    def test_list_monitors_empty(self, monitor_app: TestClient) -> None:
        resp = monitor_app.get("/api/monitors")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_monitors_with_scope(self, monitor_app: TestClient) -> None:
        monitor_app.post("/api/monitors", json={
            "name": "g", "condition_type": "metric_threshold", "params": {}, "scope": "global",
        })
        monitor_app.post("/api/monitors", json={
            "name": "r", "condition_type": "stall_window", "params": {}, "scope": "run:test",
        })
        resp = monitor_app.get("/api/monitors", params={"scope": "run:test"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "r"

    def test_delete_monitor_204(self, monitor_app: TestClient) -> None:
        create_resp = monitor_app.post("/api/monitors", json={
            "name": "del", "condition_type": "metric_threshold", "params": {},
        })
        cid = create_resp.json()["id"]
        resp = monitor_app.delete(f"/api/monitors/{cid}")
        assert resp.status_code == 204

    def test_delete_monitor_404(self, monitor_app: TestClient) -> None:
        resp = monitor_app.delete("/api/monitors/nonexistent")
        assert resp.status_code == 404

    def test_list_alerts_empty(self, monitor_app: TestClient) -> None:
        resp = monitor_app.get("/api/monitors/alerts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_wait_timeout_returns_false(self, tmp_path: Path) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.server.monitor_api import monitor_router

        store = SQLiteStore(tmp_path / "test.db")
        store.migrate(MIGRATIONS_DIR)
        engine = MonitorEngine(sqlite=store)
        engine.start()

        app = FastAPI()
        app.state.store = store
        app.state.app_settings = AppSettings()
        app.state.monitor_engine = engine
        app.include_router(monitor_router)
        client = TestClient(app)

        try:
            # Create a condition first
            create_resp = client.post("/api/monitors", json={
                "name": "wait-test", "condition_type": "metric_threshold",
                "params": {"metric": "x", "threshold": 99, "direction": "above"},
            })
            cid = create_resp.json()["id"]
            resp = client.post(f"/api/monitors/{cid}/wait", params={"timeout": "0.3"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["fired"] is False
        finally:
            engine.stop()

    def test_wait_fires_returns_true(self, tmp_path: Path) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition
        from autocontext.server.monitor_api import monitor_router

        store = SQLiteStore(tmp_path / "test.db")
        store.migrate(MIGRATIONS_DIR)
        engine = MonitorEngine(sqlite=store)
        engine.start()

        app = FastAPI()
        app.state.store = store
        app.state.app_settings = AppSettings()
        app.state.monitor_engine = engine
        app.include_router(monitor_router)
        client = TestClient(app)

        try:
            cond = MonitorCondition(
                id="cwait", name="wait-fire", condition_type=ConditionType.METRIC_THRESHOLD,
                params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
                scope="global",
            )
            store.insert_monitor_condition(cond)

            # Fire alert from another thread
            def fire() -> None:
                time.sleep(0.15)
                engine._on_event("generation_completed", {"best_score": 0.95})

            t = threading.Thread(target=fire)
            t.start()
            resp = client.post("/api/monitors/cwait/wait", params={"timeout": "5.0"})
            t.join()
            assert resp.status_code == 200
            data = resp.json()
            assert data["fired"] is True
        finally:
            engine.stop()

    def test_wait_returns_existing_alert_immediately(self, tmp_path: Path) -> None:
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition
        from autocontext.server.monitor_api import monitor_router

        store = SQLiteStore(tmp_path / "test.db")
        store.migrate(MIGRATIONS_DIR)
        engine = MonitorEngine(sqlite=store)
        engine.start()

        app = FastAPI()
        app.state.store = store
        app.state.app_settings = AppSettings()
        app.state.monitor_engine = engine
        app.include_router(monitor_router)
        client = TestClient(app)

        try:
            cond = MonitorCondition(
                id="cwait", name="wait-fire", condition_type=ConditionType.METRIC_THRESHOLD,
                params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
                scope="global",
            )
            store.insert_monitor_condition(cond)
            engine._on_event("generation_completed", {"best_score": 0.95})
            resp = client.post("/api/monitors/cwait/wait", params={"timeout": "0.01"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["fired"] is True
            assert data["alert"] is not None
        finally:
            engine.stop()

    def test_create_monitor_applies_heartbeat_default(self, tmp_path: Path) -> None:
        from autocontext.server.monitor_api import monitor_router

        store = SQLiteStore(tmp_path / "test.db")
        store.migrate(MIGRATIONS_DIR)

        app = FastAPI()
        app.state.store = store
        app.state.app_settings = AppSettings(monitor_heartbeat_timeout=42.0)
        app.state.monitor_engine = None
        app.include_router(monitor_router)
        client = TestClient(app)

        resp = client.post("/api/monitors", json={
            "name": "hb",
            "condition_type": "heartbeat_lost",
            "params": {},
            "scope": "global",
        })
        assert resp.status_code == 201
        created = store.get_monitor_condition(resp.json()["id"])
        assert created is not None
        assert created["params"]["timeout_seconds"] == 42.0

    def test_create_monitor_enforces_max_conditions(self, tmp_path: Path) -> None:
        from autocontext.server.monitor_api import monitor_router

        store = SQLiteStore(tmp_path / "test.db")
        store.migrate(MIGRATIONS_DIR)

        app = FastAPI()
        app.state.store = store
        app.state.app_settings = AppSettings(monitor_max_conditions=1)
        app.state.monitor_engine = None
        app.include_router(monitor_router)
        client = TestClient(app)

        first = client.post("/api/monitors", json={
            "name": "one",
            "condition_type": "metric_threshold",
            "params": {"metric": "best_score", "threshold": 0.8, "direction": "above"},
        })
        assert first.status_code == 201
        second = client.post("/api/monitors", json={
            "name": "two",
            "condition_type": "metric_threshold",
            "params": {"metric": "best_score", "threshold": 0.9, "direction": "above"},
        })
        assert second.status_code == 409


# ===========================================================================
# 6. Integration
# ===========================================================================


class TestMonitorIntegration:
    def test_full_metric_threshold_cycle(self, sqlite_store: SQLiteStore, tmp_path: Path) -> None:
        """Create condition -> emit event -> alert appears in SQLite."""
        from autocontext.loop.events import EventStreamEmitter
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        emitter = EventStreamEmitter(tmp_path / "events.ndjson")
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()

        try:
            cond = MonitorCondition(
                id="int1", name="threshold", condition_type=ConditionType.METRIC_THRESHOLD,
                params={"metric": "best_score", "threshold": 0.8, "direction": "above"},
                scope="global",
            )
            sqlite_store.insert_monitor_condition(cond)

            # Emit event via the emitter (which triggers the engine callback)
            emitter.emit("generation_completed", {"best_score": 0.95, "run_id": "r1"})

            # Give the callback time to complete
            time.sleep(0.1)

            alerts = sqlite_store.list_monitor_alerts(condition_id="int1")
            assert len(alerts) >= 1
            assert alerts[0]["condition_name"] == "threshold"
        finally:
            engine.stop()

    def test_full_stall_window_cycle(self, sqlite_store: SQLiteStore, tmp_path: Path) -> None:
        """Stall window requires gate_history from payload."""
        from autocontext.loop.events import EventStreamEmitter
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        emitter = EventStreamEmitter(tmp_path / "events.ndjson")
        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()

        try:
            cond = MonitorCondition(
                id="int2", name="stall", condition_type=ConditionType.STALL_WINDOW,
                params={"window": 2},
                scope="global",
            )
            sqlite_store.insert_monitor_condition(cond)

            emitter.emit("gate_decided", {
                "gate_history": ["rollback", "retry"],
            })
            time.sleep(0.1)

            alerts = sqlite_store.list_monitor_alerts(condition_id="int2")
            assert len(alerts) >= 1
        finally:
            engine.stop()

    def test_websocket_receives_alert(self, sqlite_store: SQLiteStore, tmp_path: Path) -> None:
        """Verify that monitor_alert events are broadcast to WebSocket clients via the emitter."""
        from autocontext.loop.events import EventStreamEmitter
        from autocontext.monitor.engine import MonitorEngine
        from autocontext.monitor.types import ConditionType, MonitorCondition

        emitter = EventStreamEmitter(tmp_path / "events.ndjson")
        ws_events: list[tuple[str, dict[str, object]]] = []
        emitter.subscribe(lambda e, p: ws_events.append((e, p)))

        engine = MonitorEngine(sqlite=sqlite_store, emitter=emitter)
        engine.start()

        try:
            cond = MonitorCondition(
                id="ws1", name="ws-test", condition_type=ConditionType.METRIC_THRESHOLD,
                params={"metric": "best_score", "threshold": 0.5, "direction": "above"},
                scope="global",
            )
            sqlite_store.insert_monitor_condition(cond)

            emitter.emit("generation_completed", {"best_score": 0.9})
            time.sleep(0.2)

            monitor_events = [(e, p) for e, p in ws_events if e == "monitor_alert"]
            assert len(monitor_events) >= 1
            _, payload = monitor_events[0]
            assert payload.get("condition_name") == "ws-test"
        finally:
            engine.stop()


# ===========================================================================
# 7. Settings
# ===========================================================================


class TestMonitorSettings:
    def test_defaults(self) -> None:
        s = AppSettings()
        assert s.monitor_enabled is True
        assert s.monitor_heartbeat_timeout == 300.0
        assert s.monitor_max_conditions == 100

    def test_env_var_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_MONITOR_ENABLED", "false")
        monkeypatch.setenv("AUTOCONTEXT_MONITOR_HEARTBEAT_TIMEOUT", "60.0")
        monkeypatch.setenv("AUTOCONTEXT_MONITOR_MAX_CONDITIONS", "50")

        settings = load_settings()
        assert settings.monitor_enabled is False
        assert settings.monitor_heartbeat_timeout == 60.0
        assert settings.monitor_max_conditions == 50
