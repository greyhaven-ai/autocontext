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
    GATE_MIN_SKIPPED_GROUPS,
    Scored,
    compare_to_baseline,
    expanding_folds,
    learning_curve,
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
    assert row["skip_precision"] == 0.8 and row["skipped_groups"] == 3
    assert row["skip_precision_wilson"] is not None and row["skip_precision_interval"] is not None


def row(
    model: str,
    rate: float,
    precision: float,
    low: float,
    loss: float,
    *,
    clustered_low: float | None = None,
    skipped_groups: int = GATE_MIN_SKIPPED_GROUPS,
) -> dict:
    """A summary row; the task-clustered lower bound defaults to the Wilson one."""
    return {
        "model": model,
        "skip_rate": rate,
        "skip_precision": precision,
        "skip_precision_wilson": (low, 1.0),
        "skip_precision_interval": (low if clustered_low is None else clustered_low, 1.0),
        "skipped_groups": skipped_groups,
        "log_loss": loss,
    }


def test_gate_prefers_p1_unless_beaten() -> None:
    summary = [row("p1-structural", 0.25, 0.97, 0.92, 0.40), row("p3-hybrid", 0.30, 0.97, 0.92, 0.35)]
    beaten = [{"model": "p3-hybrid", "interval": (-0.08, -0.01)}]
    not_beaten = [{"model": "p3-hybrid", "interval": (-0.05, 0.02)}]
    assert phase1_gate(summary, beaten)["model"] == "p3-hybrid"
    assert phase1_gate(summary, not_beaten)["model"] == "p1-structural"
    assert phase1_gate([row("p1-structural", 0.1, 0.97, 0.92, 0.4)], [])["go"] is False
    assert phase1_gate([row("p3-hybrid", 0.3, 0.97, 0.92, 0.4)], [])["go"] is False  # P1 missing from the ladder


def test_gate_needs_the_task_clustered_lower_bound() -> None:
    # Wilson treats the skipped rounds as independent; rounds of one task are not.
    passes_wilson_only = row("p1-structural", 0.3, 0.96, 0.92, 0.4, clustered_low=0.86)
    gate = phase1_gate([passes_wilson_only], [])
    assert gate["go"] is False and gate["model"] is None
    assert phase1_gate([row("p1-structural", 0.3, 0.96, 0.92, 0.4, clustered_low=0.90)], [])["go"] is True
    no_interval = row("p1-structural", 0.3, 0.96, 0.92, 0.4)
    no_interval["skip_precision_interval"] = None
    assert phase1_gate([no_interval], [])["go"] is False


def test_gate_needs_enough_distinct_skipped_tasks() -> None:
    few = row("p1-structural", 0.3, 0.97, 0.92, 0.4, skipped_groups=GATE_MIN_SKIPPED_GROUPS - 1)
    assert GATE_MIN_SKIPPED_GROUPS == 12
    assert phase1_gate([few], [])["go"] is False
    assert phase1_gate([row("p1-structural", 0.3, 0.97, 0.92, 0.4, skipped_groups=12)], [])["go"] is True


def test_summary_clustered_bound_falls_below_wilson_when_misses_concentrate_in_one_task() -> None:
    # 200 skipped rounds over 12 tasks at precision 0.96: all 8 misses belong to one task.
    scored = []
    for i in range(200):
        group = "bad" if i < 16 else f"g{i % 11}"
        scored.append(Scored("p1-structural", 1, i, group, failed=not (i < 8), p_fail=0.9, threshold=0.5))
    scored += [Scored("p1-structural", 1, 200 + i, f"g{i % 11}", failed=False, p_fail=0.1, threshold=0.5) for i in range(200)]
    [summary] = summarize(scored, seed=0, resamples=2000)
    assert summary["skip_precision"] == 0.96 and summary["skipped_groups"] == 12 and summary["skip_rate"] == 0.5
    assert summary["skip_precision_wilson"][0] >= 0.90 > summary["skip_precision_interval"][0]
    assert phase1_gate([summary], [])["go"] is False


def test_gate_reason_when_p1_fails_the_bar_and_another_model_qualifies() -> None:
    summary = [row("p1-structural", 0.10, 0.97, 0.92, 0.40), row("p3-hybrid", 0.30, 0.97, 0.92, 0.45)]
    reasons = {}
    for case, comparisons in (
        ("none", []),
        ("no interval", [{"model": "p3-hybrid", "interval": None}]),
        ("worse", [{"model": "p3-hybrid", "interval": (0.01, 0.09)}]),
        ("inconclusive", [{"model": "p3-hybrid", "interval": (-0.05, 0.02)}]),
    ):
        gate = phase1_gate(summary, comparisons)
        assert (gate["go"], gate["model"]) == (True, "p3-hybrid"), case
        reasons[case] = gate["reason"]
    assert "no log-loss comparison" in reasons["none"] and reasons["none"] == reasons["no interval"]
    assert "significantly worse than P1 on log-loss" in reasons["worse"]
    assert "inconclusive" in reasons["inconclusive"]
    assert len({reasons["none"], reasons["worse"], reasons["inconclusive"]}) == 3


def test_compare_to_baseline_pairs_by_fold_and_row() -> None:
    # Row 10 is scored in two folds with different predictions, and row 11 has no baseline score.
    base = [Scored("p1-structural", 1, 10, "g", True, 0.9, None), Scored("p1-structural", 2, 10, "g", True, 0.5, None)]
    model = [
        Scored("p3-hybrid", 2, 10, "g", True, 0.5, None),
        Scored("p3-hybrid", 1, 10, "g", True, 0.9, None),
        Scored("p3-hybrid", 3, 11, "h", False, 0.2, None),
    ]
    [comparison] = compare_to_baseline(base + model, seed=0, resamples=50)
    assert (comparison["model"], comparison["n"]) == ("p3-hybrid", 2)
    assert comparison["log_loss_difference"] == 0.0 and comparison["interval"] == (0.0, 0.0)


def test_learning_curve_is_reproducible_for_a_seed() -> None:
    data = synthetic(400, seed=5)
    first = learning_curve(data, "p1", halvings=2, repeats=3, seed=11)
    again = learning_curve(data, "p1", halvings=2, repeats=3, seed=11)
    assert first and first == again
    assert {r["scheme"] for r in first} == {"all", "random", "recent"}


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
    document = json.loads(result.stdout)
    assert set(document) == {"report_path", "gate", "summary"} and document["report_path"] == str(path)
    assert f"wrote {path}" in result.stderr


@pytest.mark.parametrize(
    ("flag", "value"), [("--models", "p0,p9"), ("--models", " , "), ("--curve-model", "p7"), ("--curve-model", "p1,p3")]
)
def test_cli_replay_rejects_unknown_model_keys_as_a_usage_error(tmp_path: Path, flag: str, value: str) -> None:
    db = tmp_path / "ledger.db"
    SQLiteStore(db).migrate(MIGRATIONS)
    args = ["prescreen", "replay", "--family", "fam", "--judge-identity", "j1", flag, value, "--db-path", str(db)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2 and isinstance(result.exception, SystemExit), result.output
    assert "Invalid value" in result.stderr and flag in result.stderr and result.stdout == ""


def test_cli_replay_without_data_exits_1(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    SQLiteStore(db).migrate(MIGRATIONS)
    result = CliRunner().invoke(app, ["prescreen", "replay", "--family", "fam", "--judge-identity", "j1", "--db-path", str(db)])
    assert result.exit_code == 1
