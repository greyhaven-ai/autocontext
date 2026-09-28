# autocontext/tests/test_prescreen_replay.py
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

pytest.importorskip("sklearn")

from typer.testing import CliRunner  # noqa: E402

from autocontext.cli import app  # noqa: E402
from autocontext.prescreen.dataset import Example  # noqa: E402
from autocontext.prescreen.replay import (  # noqa: E402
    Scored,
    expanding_folds,
    phase1_gate,
    run_replay,
    summarize,
)
from autocontext.storage.sqlite_store import SQLiteStore  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def synthetic(n: int, seed: int = 0) -> list[Example]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        previous = rng.random()
        fails = previous < 0.7 if rng.random() > 0.05 else previous >= 0.7
        out.append(
            Example(
                row_id=i,
                loop_id=f"L{i}",
                group=f"g{i % 20}",
                created_at=f"t{i:05d}",
                round_number=2,
                failed=fails,
                structural=(2.0, previous, previous, previous, 5.0, 1.0, 0.5, 1.0),
                text=f"shared prompt words {'stub' if previous < 0.7 else 'done'} item {i % 5}",
            )
        )
    return out


def test_expanding_folds_train_on_the_past_only() -> None:
    data = synthetic(500)
    folds = expanding_folds(data, blocks=5, min_train=100)
    assert [k for k, _, _ in folds] == [1, 2, 3, 4]
    for _, train, test in folds:
        assert max(e.row_id for e in train) < min(e.row_id for e in test)
    # 150 examples in blocks of 30: only the fold that trains on 120 rounds clears min_train=100
    assert [k for k, _, _ in expanding_folds(data[:150], blocks=5, min_train=100)] == [4]


def test_summary_counts_skips_and_precision() -> None:
    scored = [Scored("m", 1, i, f"g{i % 3}", failed=i < 8, p_fail=0.9 if i < 10 else 0.1, threshold=0.5) for i in range(20)]
    [row] = summarize(scored, seed=0, resamples=200)
    assert row["skipped"] == 10 and row["skip_rate"] == 0.5
    assert row["skip_precision"] == 0.8
    assert row["skip_precision_wilson"] is not None and row["skip_precision_interval"] is not None


def test_gate_prefers_p1_unless_beaten() -> None:
    def row(model: str, rate: float, precision: float, low: float, loss: float) -> dict:
        return {
            "model": model,
            "skip_rate": rate,
            "skip_precision": precision,
            "skip_precision_wilson": (low, 1.0),
            "log_loss": loss,
        }

    summary = [row("p1-structural", 0.25, 0.97, 0.92, 0.40), row("p3-hybrid", 0.30, 0.97, 0.92, 0.35)]
    beaten = [{"model": "p3-hybrid", "interval": (-0.08, -0.01)}]
    not_beaten = [{"model": "p3-hybrid", "interval": (-0.05, 0.02)}]
    assert phase1_gate(summary, beaten)["model"] == "p3-hybrid"
    assert phase1_gate(summary, not_beaten)["model"] == "p1-structural"
    assert phase1_gate([row("p1-structural", 0.1, 0.97, 0.92, 0.4)], [])["go"] is False
    assert phase1_gate([row("p3-hybrid", 0.3, 0.97, 0.92, 0.4)], [])["go"] is False  # P1 missing from the ladder


def test_run_replay_report_shape_and_privacy() -> None:
    data = synthetic(600, seed=3)
    report = run_replay(
        data,
        family="fam",
        judge_identity="j1",
        model_keys=("p0", "p1", "p3"),
        curve_model="p1",
        curve_halvings=1,
        curve_repeats=2,
        resamples=200,
    )
    assert set(report) == {"meta", "summary", "comparisons", "gate", "learning_curve"}
    assert report["meta"]["judge_identity"] == "j1" and "epoch" not in report["meta"]
    assert {r["model"] for r in report["summary"]} == {"p0-base-rate", "p1-structural", "p3-hybrid"}
    assert report["gate"]["go"] in (True, False)
    assert {r["scheme"] for r in report["learning_curve"]} >= {"all", "recent", "random"}
    assert "shared prompt words" not in json.dumps(report, default=str)


def _insert_loops(store: SQLiteStore, loops: int, seed: int = 0) -> None:
    rng = random.Random(seed)
    for i in range(loops):
        quality = rng.random()
        scores = [0.3, 0.95 if quality > 0.5 else 0.5, 0.95]
        for n, score in enumerate(scores, start=1):
            store.insert_judge_ledger_row(
                {
                    "loop_id": f"L{i}",
                    "run_id": "r",
                    "scenario_name": f"task{i % 10}",
                    "scenario_family": "fam",
                    "round_number": n,
                    "max_rounds": 5,
                    "quality_threshold": 0.9,
                    "evaluator_epoch": f"e{i % 3}",
                    "judge_identity": "j1",
                    "rubric_hash": "h",
                    "task_prompt_hash": "h",
                    "task_prompt": "shared task prompt",
                    "output_hash": f"{i}-{n}",
                    "output": f"answer {'good detailed' if quality > 0.5 else 'thin stub'} {n}",
                    "dimension_scores_json": json.dumps({"q": score}),
                    "score": score,
                    "passed": int(score >= 0.9),
                }
            )
            if score >= 0.9:
                break


def test_cli_replay_writes_a_report(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    store = SQLiteStore(db)
    store.migrate(MIGRATIONS)
    _insert_loops(store, 300)
    out = tmp_path / "reports"
    result = CliRunner().invoke(
        app,
        [
            "prescreen",
            "replay",
            "--family",
            "fam",
            "--judge-identity",
            "j1",
            "--models",
            "p0,p1",
            "--curve-model",
            "none",
            "--min-train",
            "50",
            "--resamples",
            "100",
            "--db-path",
            str(db),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    [path] = list(out.glob("replay-fam-*.json"))
    assert "shared task prompt" not in path.read_text()
    meta = json.loads(path.read_text())["meta"]
    # Every loop has an eligible round, and the three evaluator epochs are pooled under the one judge identity.
    assert meta["judge_identity"] == "j1" and meta["examples"] >= 300


def test_cli_replay_without_data_exits_1(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    SQLiteStore(db).migrate(MIGRATIONS)
    result = CliRunner().invoke(app, ["prescreen", "replay", "--family", "fam", "--judge-identity", "j1", "--db-path", str(db)])
    assert result.exit_code == 1
