from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from autocontext.ambient.charter_io import load_charter
from autocontext.ambient.datasets import DatasetStore
from autocontext.ambient.proposals import CharterProposal, ProposalStore
from autocontext.cli import app
from autocontext.cli_ambient import ambient_app

runner = CliRunner()


def _init(tmp_path: Path) -> Path:
    charter_path = tmp_path / "ambient-charter.yaml"
    result = runner.invoke(
        app,
        ["ambient", "init", "--charter-path", str(charter_path)],
        input="oss\npropose\nn\nn\ncompetitor@grid_ctf\nQwen/Qwen2.5-3B-Instruct\n8\n24\n200\n",
    )
    assert result.exit_code == 0, result.output
    return charter_path


def test_init_writes_charter(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    assert charter_path.exists()
    assert "competitor@grid_ctf" in charter_path.read_text(encoding="utf-8")


def test_init_refuses_overwrite_without_force(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(app, ["ambient", "init", "--charter-path", str(charter_path)])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_init_rejects_invalid_numeric_input(tmp_path: Path) -> None:
    charter_path = tmp_path / "ambient-charter.yaml"
    result = runner.invoke(
        app,
        ["ambient", "init", "--charter-path", str(charter_path)],
        input="oss\npropose\nn\nn\ncompetitor@grid_ctf\nQwen/Qwen2.5-3B-Instruct\nabc\n24\n200\n",
    )
    assert result.exit_code == 1
    assert "invalid interview input" in result.output
    assert not charter_path.exists()


def test_status_reports_stages(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        app,
        ["ambient", "status", "--charter-path", str(charter_path), "--db-path", str(tmp_path / "a.sqlite3")],
    )
    assert result.exit_code == 0, result.output
    for stage in ("ingest", "curate", "advise", "train", "evaluate"):
        assert stage in result.output


def test_status_missing_charter_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ambient", "status", "--charter-path", str(tmp_path / "none.yaml")])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_run_with_max_cycles_terminates(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "ambient",
            "run",
            "--charter-path",
            str(charter_path),
            "--db-path",
            str(tmp_path / "a.sqlite3"),
            "--events-path",
            str(tmp_path / "events.ndjson"),
            "--poll-seconds",
            "0",
            "--max-cycles",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "events.ndjson").exists()


def test_once_runs_single_stage(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "ambient",
            "once",
            "ingest",
            "--charter-path",
            str(charter_path),
            "--db-path",
            str(tmp_path / "a.sqlite3"),
            "--events-path",
            str(tmp_path / "events.ndjson"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "processed" in result.output


def test_status_does_not_requeue_running_jobs(tmp_path: Path) -> None:
    from autocontext.ambient.queue import AmbientQueue

    charter_path = _init(tmp_path)
    db_path = tmp_path / "a.sqlite3"
    queue = AmbientQueue(db_path)
    queue.enqueue("ingest", "poll_source", {})
    assert queue.claim("ingest") is not None  # in-flight in another process
    result = runner.invoke(
        app,
        ["ambient", "status", "--charter-path", str(charter_path), "--db-path", str(db_path)],
    )
    assert result.exit_code == 0, result.output
    assert queue.depth("ingest") == 0  # still running, not yanked back


def test_run_rejects_negative_poll_seconds(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "ambient",
            "run",
            "--charter-path",
            str(charter_path),
            "--db-path",
            str(tmp_path / "a.sqlite3"),
            "--events-path",
            str(tmp_path / "events.ndjson"),
            "--poll-seconds",
            "-1",
        ],
    )
    assert result.exit_code != 0


def test_once_ingest_pulls_from_runs_db(tmp_path: Path) -> None:
    import sqlite3

    from autocontext.ambient.trace_store import TraceStore

    charter_path = _init(tmp_path)
    runs_db = tmp_path / "runs.sqlite3"
    conn = sqlite3.connect(runs_db)
    conn.executescript(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, target_generations INTEGER NOT NULL, "
        "executor_mode TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "CREATE TABLE generations (run_id TEXT NOT NULL, generation_index INTEGER NOT NULL, mean_score REAL NOT NULL, "
        "best_score REAL NOT NULL, gate_decision TEXT NOT NULL, status TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "PRIMARY KEY (run_id, generation_index));"
    )
    conn.execute(
        "INSERT INTO runs (run_id, scenario, target_generations, executor_mode, status) "
        "VALUES ('r1', 'grid_ctf', 1, 'local', 'completed')"
    )
    conn.execute(
        "INSERT INTO generations (run_id, generation_index, mean_score, best_score, gate_decision, status) "
        "VALUES ('r1', 0, 1.0, 2.0, 'advance', 'completed')"
    )
    conn.commit()
    conn.close()
    db_path = tmp_path / "ambient.sqlite3"
    result = runner.invoke(
        app,
        [
            "ambient",
            "once",
            "ingest",
            "--charter-path",
            str(charter_path),
            "--db-path",
            str(db_path),
            "--events-path",
            str(tmp_path / "events.ndjson"),
            "--runs-db",
            str(runs_db),
            "--otel-feed-dir",
            str(tmp_path / "feed"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "processed=1" in result.output
    assert TraceStore(db_path).count() == 1


_FIXTURE_TARGET_NAME = "competitor-grid_ctf"


def test_once_curate_runs_clean_on_empty_stores(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "ambient",
            "once",
            "curate",
            "--charter-path",
            str(charter_path),
            "--db-path",
            str(tmp_path / "ambient.sqlite3"),
            "--events-path",
            str(tmp_path / "events.ndjson"),
            "--runs-db",
            str(tmp_path / "runs.sqlite3"),
            "--otel-feed-dir",
            str(tmp_path / "feed"),
            "--datasets-dir",
            str(tmp_path / "datasets"),
            "--proposals-path",
            str(tmp_path / "proposals.jsonl"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "processed=0" in result.output


def test_once_train_runs_clean_on_empty_stores(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "ambient",
            "once",
            "train",
            "--charter-path",
            str(charter_path),
            "--db-path",
            str(tmp_path / "ambient.sqlite3"),
            "--events-path",
            str(tmp_path / "events.ndjson"),
            "--runs-db",
            str(tmp_path / "runs.sqlite3"),
            "--otel-feed-dir",
            str(tmp_path / "feed"),
            "--datasets-dir",
            str(tmp_path / "datasets"),
            "--proposals-path",
            str(tmp_path / "proposals.jsonl"),
            "--registry-dir",
            str(tmp_path / "registry"),
            "--usage-db",
            str(tmp_path / "usage.sqlite3"),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--checkpoints-dir",
            str(tmp_path / "checkpoints"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "processed=0" in result.output


def test_status_shows_candidates_row(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        app,
        [
            "ambient",
            "status",
            "--charter-path",
            str(charter_path),
            "--db-path",
            str(tmp_path / "ambient.sqlite3"),
            "--events-path",
            str(tmp_path / "events.ndjson"),
            "--runs-db",
            str(tmp_path / "runs.sqlite3"),
            "--otel-feed-dir",
            str(tmp_path / "feed"),
            "--datasets-dir",
            str(tmp_path / "datasets"),
            "--proposals-path",
            str(tmp_path / "proposals.jsonl"),
            "--registry-dir",
            str(tmp_path / "registry"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "candidates=0" in result.output


def test_status_shows_target_dataset_rows(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)  # the fixture charter contains at least one target
    datasets = DatasetStore(tmp_path / "datasets")
    manifest = datasets.load_manifest(_FIXTURE_TARGET_NAME)
    datasets.save_manifest(manifest.model_copy(update={"record_count": 4, "mean_score": 0.75}))
    result = runner.invoke(
        app,
        [
            "ambient",
            "status",
            "--charter-path",
            str(charter_path),
            "--db-path",
            str(tmp_path / "ambient.sqlite3"),
            "--events-path",
            str(tmp_path / "events.ndjson"),
            "--runs-db",
            str(tmp_path / "runs.sqlite3"),
            "--otel-feed-dir",
            str(tmp_path / "feed"),
            "--datasets-dir",
            str(tmp_path / "datasets"),
            "--proposals-path",
            str(tmp_path / "proposals.jsonl"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert _FIXTURE_TARGET_NAME in result.output
    assert "4" in result.output


def _pending_proposal(tmp_path: Path, proposal_id: str = "add-target-lean-1234") -> ProposalStore:
    store = ProposalStore(tmp_path / "proposals.jsonl")
    store.append(
        CharterProposal(
            proposal_id=proposal_id,
            kind="add_target",
            payload={
                "name": "lean-auto",
                "kind": "task_family",
                "selector": "lean_prover",
                "base_model": "qwen2.5-3b",
                "method": "sft-distill",
                "min_dataset_records": 10,
                "eval_suite": "anchor-v1",
            },
            rationale="5 eligible frontier traces for scenario lean_prover at mean score 0.90",
        )
    )
    return store


def test_proposals_lists_pending(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    _pending_proposal(tmp_path)
    result = runner.invoke(
        ambient_app,
        ["proposals", "--charter-path", str(charter_path), "--proposals-path", str(tmp_path / "proposals.jsonl")],
    )
    assert result.exit_code == 0
    assert "add-target-lean-1234" in result.output


def test_proposals_reports_when_empty(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        ambient_app,
        ["proposals", "--charter-path", str(charter_path), "--proposals-path", str(tmp_path / "proposals.jsonl")],
    )
    assert result.exit_code == 0
    assert "no pending proposals" in result.output


def test_approve_applies_and_marks(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    store = _pending_proposal(tmp_path)
    result = runner.invoke(
        ambient_app,
        [
            "approve",
            "add-target-lean-1234",
            "--charter-path",
            str(charter_path),
            "--proposals-path",
            str(tmp_path / "proposals.jsonl"),
        ],
    )
    assert result.exit_code == 0
    updated = load_charter(charter_path)
    assert any(target.name == "lean-auto" for target in updated.targets)
    assert store.pending() == []


def test_approve_unknown_id_exits_one(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    result = runner.invoke(
        ambient_app,
        ["approve", "nope", "--charter-path", str(charter_path), "--proposals-path", str(tmp_path / "proposals.jsonl")],
    )
    assert result.exit_code == 1


def test_approve_failure_leaves_proposal_pending(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    store = _pending_proposal(tmp_path)
    # first approval consumes it; re-adding the same target must fail
    runner.invoke(
        ambient_app,
        [
            "approve",
            "add-target-lean-1234",
            "--charter-path",
            str(charter_path),
            "--proposals-path",
            str(tmp_path / "proposals.jsonl"),
        ],
    )
    store.append(
        CharterProposal(
            proposal_id="dup-1",
            kind="add_target",
            payload={
                "name": "lean-auto",
                "kind": "task_family",
                "selector": "lean_prover",
                "base_model": "qwen2.5-3b",
                "method": "sft-distill",
                "min_dataset_records": 10,
                "eval_suite": "anchor-v1",
            },
            rationale="duplicate",
        )
    )
    result = runner.invoke(
        ambient_app,
        ["approve", "dup-1", "--charter-path", str(charter_path), "--proposals-path", str(tmp_path / "proposals.jsonl")],
    )
    assert result.exit_code == 1
    assert len(store.pending()) == 1  # dup-1 stays pending, nothing was marked


def test_reject_marks_rejected(tmp_path: Path) -> None:
    charter_path = _init(tmp_path)
    store = _pending_proposal(tmp_path)
    result = runner.invoke(
        ambient_app,
        [
            "reject",
            "add-target-lean-1234",
            "--charter-path",
            str(charter_path),
            "--proposals-path",
            str(tmp_path / "proposals.jsonl"),
        ],
    )
    assert result.exit_code == 0
    assert store.pending() == []
