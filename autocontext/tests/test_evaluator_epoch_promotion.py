from __future__ import annotations

from pathlib import Path

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.evaluator_epoch_promotion import ReviewerDecision, promote_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry
from autocontext.execution.rubric_calibration import (
    AlignmentResult,
    AlignmentTolerance,
    CalibrationReport,
    JudgeVarianceResult,
)
from autocontext.storage.sqlite_store import SQLiteStore

_MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def _charter(autonomy: str) -> Charter:
    # A non-registered target name ("t1") is used consistently: a target name that collides with a
    # registered scenario is rejected by Charter's collision validator, and decide() looks the target
    # up by name == the scenario passed to promote_evaluator_epoch.
    return Charter(
        tier="oss",
        autonomy=autonomy,  # type: ignore[arg-type]
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=[
            CharterTarget(
                name="t1",
                kind="role",
                selector="competitor@grid_ctf",
                base_model="Qwen/Qwen2.5-3B-Instruct",
                min_dataset_records=10,
                eval_suite="grid_ctf_holdout",
                autonomy=autonomy,  # type: ignore[arg-type]
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=8.0, window_hours=24, disk_quota_gb=10.0),
    )


def _report(mae: float, epoch_id: str) -> CalibrationReport:
    return CalibrationReport(
        domain="t1",
        num_anchors=3,
        alignment=AlignmentResult(mean_absolute_error=mae, bias=0.0, correlation=0.95, num_pairs=3, per_anchor_errors=[]),
        variance=JudgeVarianceResult(mean=0.5, variance=0.0, std_dev=0.0, range=0.0, num_samples=3),
        calibrated=True,
        evaluator_epoch=epoch_id,
    )


def _reg(tmp_path: Path) -> tuple[EvaluatorEpochRegistry, object, object]:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe("t1", e1, now_fn=lambda: "t0")  # active (bootstrap)
    reg.observe("t1", e2, now_fn=lambda: "t1")  # candidate
    return reg, e1, e2


_TOL = AlignmentTolerance(domain="t1", max_mean_absolute_error=0.12, max_bias=0.08, min_correlation=0.7)


def test_full_autonomy_passing_calibration_activates(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        "t1",
        e2.epoch_id,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "activated"
    assert reg.active_for("t1").epoch_id == e2.epoch_id
    assert reg.load("t1", e2.epoch_id).promotion["previous_active"] == e1.epoch_id


def test_full_autonomy_failing_calibration_blocked(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        "t1",
        e2.epoch_id,
        calibration_report=_report(0.5, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "blocked"
    assert reg.active_for("t1").epoch_id == e1.epoch_id  # unchanged


def test_propose_requires_review(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        "t1",
        e2.epoch_id,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("propose"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "pending_review"
    assert reg.active_for("t1").epoch_id == e1.epoch_id
    assert reg.load("t1", e2.epoch_id).promotion["requires_review"] is True


def test_reviewer_approved_activates(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    decision = ReviewerDecision(outcome="approved", reviewed_by="jay", reviewed_at="t3")
    out = promote_evaluator_epoch(
        reg,
        "t1",
        e2.epoch_id,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("propose"),
        reviewer_decision=decision,
        now_fn=lambda: "t3",
    )
    assert out.outcome == "activated"
    assert reg.load("t1", e2.epoch_id).promotion["decision"]["reviewed_by"] == "jay"


def test_reviewer_rejected_stays(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    decision = ReviewerDecision(outcome="rejected", reviewed_by="jay", reviewed_at="t3")
    out = promote_evaluator_epoch(
        reg,
        "t1",
        e2.epoch_id,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("propose"),
        reviewer_decision=decision,
        now_fn=lambda: "t3",
    )
    assert out.outcome == "rejected"
    assert reg.active_for("t1").epoch_id == e1.epoch_id


def test_missing_candidate_noop(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        "t1",
        "f" * 64,
        calibration_report=_report(0.05, "f" * 64),
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "noop"


def test_activation_clears_quarantine_when_sqlite_provided(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    store = SQLiteStore(tmp_path / "runs.sqlite3")
    store.migrate(_MIGRATIONS)
    store.create_run("run-t1", "t1", 1, "local", agent_provider="anthropic")
    store.upsert_generation(
        "run-t1",
        1,
        mean_score=0.9,
        best_score=0.9,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision="completed",
        status="completed",
        evaluator_epoch=e2.epoch_id,
        quarantined=True,
    )
    out = promote_evaluator_epoch(
        reg,
        "t1",
        e2.epoch_id,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        sqlite=store,
        now_fn=lambda: "t2",
    )
    assert out.outcome == "activated"
    assert store.get_generation_metrics("run-t1")[0]["quarantined"] is None
