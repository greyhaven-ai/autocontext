"""CLI stale-epoch lineage surfacing for ``show`` + ``status`` (AC-885 Slice D1).

A generation scored under a superseded evaluator epoch must render as ``stale`` once a newer epoch is
promoted active, without any re-score (read-only surfacing). Rows with no lineage under an active epoch
are ``unknown``; a scenario with no active epoch yields ``no_active_epoch`` for every row.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry

runner = CliRunner()

_SCENARIO = "grid_ctf"
_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "t.sqlite3"))


def _store(tmp_path: Path):
    from autocontext.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(tmp_path / "t.sqlite3")
    store.migrate(_MIGRATIONS)
    return store


def _seed_epochs(tmp_path: Path) -> tuple[str, str]:
    reg = EvaluatorEpochRegistry(tmp_path / "knowledge" / "_evaluator_epochs")
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe(_SCENARIO, e1, now_fn=lambda: "t0")  # active (bootstrap)
    reg.observe(_SCENARIO, e2, now_fn=lambda: "t1")  # candidate
    reg.activate(_SCENARIO, e2.epoch_id)  # promote e2 -> e1-scored rows now stale
    return e1.epoch_id, e2.epoch_id


def _seed_run(tmp_path: Path, run_id: str, epoch_id: str | None) -> None:
    store = _store(tmp_path)
    store.create_run(run_id, _SCENARIO, 1, "local")
    store.upsert_generation(
        run_id,
        1,
        mean_score=0.5,
        best_score=0.6,
        elo=1000.0,
        wins=1,
        losses=0,
        gate_decision="pass",
        status="completed",
        evaluator_epoch=epoch_id,
    )


def test_show_json_flags_stale_generation(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    e1, e2 = _seed_epochs(tmp_path)
    _seed_run(tmp_path, "run-stale", e1)

    result = runner.invoke(app, ["show", "run-stale", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active_evaluator_epoch"] == e2
    gen = payload["generations"][0]
    assert gen["evaluator_epoch"] == e1
    assert gen["evaluator_epoch_status"] == "stale"
    assert gen["quarantined"] in (False, True)


def test_status_json_flags_stale_generation(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    e1, e2 = _seed_epochs(tmp_path)
    _seed_run(tmp_path, "run-stale", e1)

    result = runner.invoke(app, ["status", "run-stale", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active_evaluator_epoch"] == e2
    gen = payload["generations"][0]
    assert gen["evaluator_epoch"] == e1
    assert gen["evaluator_epoch_status"] == "stale"
    assert gen["quarantined"] in (False, True)


def test_show_json_no_active_epoch(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    # No epochs registered for the scenario -> nothing to compare against.
    _seed_run(tmp_path, "run-bare", "e1")

    result = runner.invoke(app, ["show", "run-bare", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active_evaluator_epoch"] is None
    gen = payload["generations"][0]
    assert gen["evaluator_epoch_status"] == "no_active_epoch"
