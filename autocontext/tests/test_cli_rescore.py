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


def test_rescore_missing_run_does_not_create_db(tmp_path: Path, monkeypatch) -> None:
    # Report-only: a missing-run invocation against a nonexistent database must NOT materialize one.
    _env(monkeypatch, tmp_path)
    db_path = tmp_path / "t.sqlite3"
    assert not db_path.exists()
    result = runner.invoke(app, ["rescore", "does-not-exist"])
    assert result.exit_code == 1
    assert not db_path.exists()  # no database was created


def test_rescore_build_failure_is_per_generation_error(tmp_path: Path, monkeypatch) -> None:
    # A _build_score_fn failure (custom-task load / prepare_context) must become a per-generation
    # `error` report, not an uncaught exit, and other flow stays fail-safe (exit 0).
    _env(monkeypatch, tmp_path)
    _e1, _e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-buildfail", _e1, with_output=True)

    def boom(scenario, settings):  # noqa: ANN001, ANN202
        raise RuntimeError("spec load blew up")

    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", boom)
    result = runner.invoke(app, ["rescore", "run-buildfail", "--json"])
    assert result.exit_code == 0, result.output
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["status"] == "error"
    assert "spec load blew up" in gen["reason"]


def test_rescore_rich_surfaces_epoch_drift_warning(tmp_path: Path, monkeypatch) -> None:
    # When the current evaluator produces an epoch that does NOT match the active epoch, the rich
    # (non-JSON) output must surface the drift, not silently show `revalidated`.
    _env(monkeypatch, tmp_path)
    _e1, _e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-drift", _e1, with_output=True)
    # Fresh score under a DIFFERENT epoch than active (spec has drifted).
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", _fixed_build(0.5, "e_drifted_epoch"))

    result = runner.invoke(app, ["rescore", "run-drift"])
    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "Warning" in normalized
    assert "does NOT match the active epoch" in normalized


def test_rescore_loads_custom_scenarios_from_configured_root(tmp_path: Path, monkeypatch) -> None:
    # _build_score_fn must load custom agent tasks from settings.knowledge_root (not the import-time
    # relative knowledge/), so a non-default-root deployment resolves its own scenarios.
    _env(monkeypatch, tmp_path)
    _e1, e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-custom", _e1, with_output=True)

    seen_roots: list[Path] = []

    def spy_loader(knowledge_root: Path):
        seen_roots.append(knowledge_root)
        return {}

    # Let the REAL _build_score_fn run; patch only its inner dependencies.
    monkeypatch.setattr("autocontext.scenarios.custom.registry.load_all_custom_scenarios", spy_loader)
    monkeypatch.setattr("autocontext.cli._is_agent_task", lambda name: False)

    result = runner.invoke(app, ["rescore", "run-custom", "--json"])
    assert result.exit_code == 0, result.output
    assert seen_roots == [tmp_path / "knowledge"]  # loaded from the CONFIGURED root
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["status"] == "skipped_no_evaluator"


def _read_revisions(tmp_path: Path, run_id: str, generation_index: int) -> list[dict]:
    store = _store(tmp_path)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM generation_score_revisions WHERE run_id = ? AND generation_index = ? ORDER BY id",
            (run_id, generation_index),
        ).fetchall()
        return [dict(r) for r in rows]


def test_rescore_apply_records_matching_audit_revision(tmp_path: Path, monkeypatch) -> None:
    # --apply on a stale generation whose re-score matches the active epoch APPENDS an audit revision:
    # the generation row is left UNCHANGED (append-only), the revision archives the current values, and
    # the output marks it recorded.
    _env(monkeypatch, tmp_path)
    e1, e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-apply", e1, with_output=True)
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", _fixed_build(0.55, e2))

    result = runner.invoke(app, ["rescore", "run-apply", "--apply", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] == [1]
    gen = payload["generations"][0]
    assert gen["applied"] is True

    store = _store(tmp_path)
    row = store.get_generation("run-apply", 1)
    assert row is not None
    # The live score of record is NOT changed by --apply (append-only audit).
    assert row["best_score"] == 0.6
    assert row["evaluator_epoch"] == e1
    assert row["quarantined"] is None  # as seeded; --apply never touches the quarantine marker

    revisions = _read_revisions(tmp_path, "run-apply", 1)
    assert len(revisions) == 1
    assert revisions[0]["revision_epoch"] == e2
    assert revisions[0]["revision_score"] == 0.55
    assert revisions[0]["previous_epoch"] == e1
    assert revisions[0]["previous_score"] == 0.6


def test_rescore_apply_skips_drifted(tmp_path: Path, monkeypatch) -> None:
    # --apply on a DRIFTED re-score (fresh epoch != active) writes nothing: the generation row is
    # unchanged, no revision is written, and the report shows applied=false + new_matches_active=false.
    _env(monkeypatch, tmp_path)
    e1, _e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-drift-apply", e1, with_output=True)
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", _fixed_build(0.5, "e_other"))

    result = runner.invoke(app, ["rescore", "run-drift-apply", "--apply", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] == []
    gen = payload["generations"][0]
    assert gen["applied"] is False
    assert gen["new_matches_active"] is False

    store = _store(tmp_path)
    row = store.get_generation("run-drift-apply", 1)
    assert row is not None
    assert row["best_score"] == 0.6  # unchanged
    assert row["evaluator_epoch"] == e1  # unchanged
    assert _read_revisions(tmp_path, "run-drift-apply", 1) == []


def test_rescore_without_apply_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    # No --apply must write nothing even when the re-score matches the active epoch (D2a preserved).
    _env(monkeypatch, tmp_path)
    e1, e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-noapply", e1, with_output=True)
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", _fixed_build(0.55, e2))

    result = runner.invoke(app, ["rescore", "run-noapply", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] == []
    gen = payload["generations"][0]
    assert gen["applied"] is False

    store = _store(tmp_path)
    row = store.get_generation("run-noapply", 1)
    assert row is not None
    assert row["best_score"] == 0.6  # unchanged
    assert row["evaluator_epoch"] == e1  # unchanged
    assert _read_revisions(tmp_path, "run-noapply", 1) == []


def test_rescore_apply_records_by(tmp_path: Path, monkeypatch) -> None:
    # --by is recorded on the revision's created_by.
    _env(monkeypatch, tmp_path)
    e1, e2 = _seed_epochs_active_e2(tmp_path)
    _seed_generation(tmp_path, "run-by", e1, with_output=True)
    monkeypatch.setattr("autocontext.cli_rescore._build_score_fn", _fixed_build(0.55, e2))

    result = runner.invoke(app, ["rescore", "run-by", "--apply", "--by", "jay", "--json"])
    assert result.exit_code == 0, result.output
    revisions = _read_revisions(tmp_path, "run-by", 1)
    assert len(revisions) == 1
    assert revisions[0]["created_by"] == "jay"


class _FakeAgentTask:
    """Minimal agent-task double whose evaluate_output records the active hook bus at call time."""

    hook_active_during_eval = False
    eval_epoch = "e2-fake"

    def initial_state(self, seed=None):  # noqa: ANN001, ANN201
        return {}

    def prepare_context(self, state):  # noqa: ANN001, ANN201
        return state

    def get_task_prompt(self, state):  # noqa: ANN001, ANN201
        return "task"

    def get_rubric(self):  # noqa: ANN201
        return "rubric"

    def describe_task(self):  # noqa: ANN201
        return "fake"

    def evaluate_output(self, output, state, **kwargs):  # noqa: ANN001, ANN003, ANN201
        from autocontext.extensions import get_current_hook_bus
        from autocontext.scenarios.agent_task import AgentTaskResult

        type(self).hook_active_during_eval = get_current_hook_bus() is not None
        return AgentTaskResult(score=0.42, reasoning="r", evaluator_epoch=type(self).eval_epoch)


def test_rescore_evaluates_inside_hook_bus(tmp_path: Path, monkeypatch) -> None:
    # The re-score must run inside the configured hook bus so BEFORE/AFTER_JUDGE hooks reproduce the
    # production evaluator. Assert evaluate_output sees an active hook bus.
    _env(monkeypatch, tmp_path)
    _e1, e2 = _seed_epochs_active_e2(tmp_path)
    _FakeAgentTask.eval_epoch = e2
    _FakeAgentTask.hook_active_during_eval = False
    _seed_generation(tmp_path, "run-hooks", _e1, with_output=True)

    from autocontext.scenarios import SCENARIO_REGISTRY

    monkeypatch.setitem(SCENARIO_REGISTRY, _SCENARIO, _FakeAgentTask)
    monkeypatch.setattr("autocontext.cli._is_agent_task", lambda name: True)
    monkeypatch.setattr("autocontext.scenarios.custom.registry.load_all_custom_scenarios", lambda root: {})
    # A real (empty) hook bus so get_current_hook_bus() is not None inside active_hook_bus.
    from autocontext.extensions import HookBus

    monkeypatch.setattr("autocontext.loop.runner_hooks.initialize_hook_bus", lambda settings: (HookBus(), []))

    result = runner.invoke(app, ["rescore", "run-hooks", "--json"])
    assert result.exit_code == 0, result.output
    gen = json.loads(result.stdout)["generations"][0]
    assert gen["status"] == "revalidated"
    assert _FakeAgentTask.hook_active_during_eval is True
