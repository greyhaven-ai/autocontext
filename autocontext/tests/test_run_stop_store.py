from __future__ import annotations

from pathlib import Path

from autocontext.storage.sqlite_store import SQLiteStore


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.sqlite3")
    store.migrate(Path("migrations"))
    return store


def _seed_running_run(store: SQLiteStore, run_id: str) -> None:
    store.create_run(run_id, scenario="demo", generations=1, executor_mode="local")


def test_mark_run_stopped_transitions_running_to_stopped(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_running_run(store, "r1")

    assert store.mark_run_stopped("r1") is True

    run = store.get_run("r1")
    assert run is not None
    assert run["status"] == "stopped"


def test_mark_run_stopped_is_idempotent_first_wins(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_running_run(store, "r1")

    assert store.mark_run_stopped("r1") is True
    # Second call loses the race: run is no longer 'running'.
    assert store.mark_run_stopped("r1") is False

    run = store.get_run("r1")
    assert run is not None
    assert run["status"] == "stopped"


def test_mark_run_completed_does_not_revert_stopped_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_running_run(store, "r1")

    assert store.mark_run_stopped("r1") is True
    store.mark_run_completed("r1")

    run = store.get_run("r1")
    assert run is not None
    assert run["status"] == "stopped"


def test_mark_run_completed_still_completes_running_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_running_run(store, "r1")

    store.mark_run_completed("r1")

    run = store.get_run("r1")
    assert run is not None
    assert run["status"] == "completed"
