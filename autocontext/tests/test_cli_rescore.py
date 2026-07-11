"""CLI integration for ``autoctx rescore`` (AC-885 Slice D2a).

The scoring seam (``_build_score_fn``) is patched to inject a deterministic ``score_fn`` so no
provider/network/paid-LLM call is made. The command is report-only: it re-scores stale generations
under the current evaluator and prints old-vs-new score + epoch, writing nothing.
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


def _seed_epochs_active_e2(tmp_path: Path) -> tuple[str, str]:
    """Seed e1 (bootstrap active) + e2 (candidate), then promote e2 so e1-scored rows are stale."""
    reg = EvaluatorEpochRegistry(tmp_path / "knowledge" / "_evaluator_epochs")
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe(_SCENARIO, e1, now_fn=lambda: "t0")
    reg.observe(_SCENARIO, e2, now_fn=lambda: "t1")
    reg.activate(_SCENARIO, e2.epoch_id)
    return e1.epoch_id, e2.epoch_id


def _seed_generation(tmp_path: Path, run_id: str, epoch_id: str | None, *, with_output: bool) -> None:
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
    if with_output:
        store.append_agent_output(run_id, 1, "competitor", "strategy text")


def _fixed_build(score: float, epoch: str | None):
    def build(scenario: str, settings):  # noqa: ANN001, ANN202 - test seam signature parity
        def fn(artifact: str) -> tuple[float | None, str | None]:
            return score, epoch

        return fn

    return build


def test_rescore_revalidated_json(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    e1, e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-stale", e1, with_output=True)
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", _fixed_build(0.55, e2))

    result = runner.invoke(app, ["rescore", "run-stale", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active_evaluator_epoch"] == e2
    gen = payload["generations"][0]
    assert gen["status"] == "revalidated"
    assert gen["original_epoch"] == e1
    assert gen["new_epoch"] == e2
    assert gen["was_stale"] is True
    assert gen["new_matches_active"] is True
    assert gen["new_score"] == 0.55


def test_rescore_no_active_epoch(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    # No epochs registered for the scenario -> nothing to re-score against.
    _seed_generation(tmp_path, "run-bare", "e1", with_output=True)
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", _fixed_build(0.55, "e2"))

    result = runner.invoke(app, ["rescore", "run-bare", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active_evaluator_epoch"] is None
    gen = payload["generations"][0]
    assert gen["status"] == "skipped_no_active_epoch"
    assert gen["was_stale"] is False


def test_rescore_stale_no_artifact(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    _e1, e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-noart", _e1, with_output=False)
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", _fixed_build(0.55, e2))

    result = runner.invoke(app, ["rescore", "run-noart", "--json"])
    assert result.exit_code == 0, result.output
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["status"] == "skipped_no_artifact"


def test_rescore_missing_run(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["rescore", "does-not-exist", "--json"])
    assert result.exit_code == 1
    # --json errors emit a JSON object, not a bare string (matches sibling --json commands).
    assert '"error"' in result.output
    assert json.loads(result.output.strip()) == {"error": "run 'does-not-exist' not found"}


def test_rescore_non_agent_task_no_evaluator(tmp_path: Path, monkeypatch) -> None:
    _env(monkeypatch, tmp_path)
    _e1, e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-nonagent", _e1, with_output=True)
    # _build_score_fn returns None for a non-agent-task scenario.
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", lambda scenario, settings: None)

    result = runner.invoke(app, ["rescore", "run-nonagent", "--json"])
    assert result.exit_code == 0, result.output
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["status"] == "skipped_no_evaluator"
