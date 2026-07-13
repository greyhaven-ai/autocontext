"""CLI recorded-re-score surfacing for ``show`` + ``status`` (AC-885 Slice D2c).

A generation whose latest active-epoch re-score was recorded by ``rescore --apply`` must surface that
revision (score, who, when) alongside the unchanged live score. Read-only: the live ``best_score`` is
never modified. Every generation dict always gains the four fields (null when no active-epoch revision).
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


def _seed_run_with_revision(tmp_path: Path) -> tuple[str, str]:
    """Seed a stale generation (scored under e1, e2 active) plus an active-epoch re-score under e2."""
    e1, e2 = _seed_epochs(tmp_path)
    _seed_run(tmp_path, "run-rev", e1)
    store = _store(tmp_path)
    assert store.record_rescore_revision("run-rev", 1, 0.55, e2, created_by="jay")
    return e1, e2


def test_show_json_surfaces_revision(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    _seed_run_with_revision(tmp_path)

    result = runner.invoke(app, ["show", "run-rev", "--json"])
    assert result.exit_code == 0, result.output
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["has_active_revision"] is True
    assert gen["revised_score"] == 0.55
    assert gen["revised_by"] == "jay"
    assert gen["revised_at"] is not None
    # The live score of record is unchanged (read-only surfacing).
    assert gen["best_score"] == 0.6


def test_status_json_surfaces_revision(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    _seed_run_with_revision(tmp_path)

    result = runner.invoke(app, ["status", "run-rev", "--json"])
    assert result.exit_code == 0, result.output
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["has_active_revision"] is True
    assert gen["revised_score"] == 0.55
    assert gen["revised_by"] == "jay"
    assert gen["revised_at"] is not None
    assert gen["best_score"] == 0.6


def test_show_rich_surfaces_revision(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    _seed_run_with_revision(tmp_path)

    result = runner.invoke(app, ["show", "run-rev"])
    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "0.5500" in normalized  # revised-score cell
    assert "re-score recorded" in normalized  # the note line


def test_status_rich_surfaces_revision(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    _seed_run_with_revision(tmp_path)

    result = runner.invoke(app, ["status", "run-rev"])
    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "0.5500" in normalized
    assert "re-score recorded" in normalized


def test_show_json_no_revision(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    e1, _e2 = _seed_epochs(tmp_path)
    _seed_run(tmp_path, "run-plain", e1)

    result = runner.invoke(app, ["show", "run-plain", "--json"])
    assert result.exit_code == 0, result.output
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["has_active_revision"] is False
    assert gen["revised_score"] is None
    assert gen["revised_by"] is None
    assert gen["revised_at"] is None


def test_status_json_no_active_epoch_still_has_fields(tmp_path: Path, monkeypatch) -> None:
    """A scenario with no active epoch still yields the four null-revision fields (consistent shape)."""
    _env(monkeypatch, tmp_path)
    _seed_run(tmp_path, "run-bare", "e1")  # no epochs registered -> active_epoch_id None

    result = runner.invoke(app, ["status", "run-bare", "--json"])
    assert result.exit_code == 0, result.output
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["has_active_revision"] is False
    assert gen["revised_score"] is None
