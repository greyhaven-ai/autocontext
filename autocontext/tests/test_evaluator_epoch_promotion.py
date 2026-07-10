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

# The registry/sqlite are keyed by the real scenario name ("grid_ctf"); the charter target the policy
# is looked up by is a distinct name ("competitor-local"). A valid charter FORBIDS a target name that
# collides with a registered scenario, so the two must never be conflated (see FIX 1 regression test).
_SCENARIO = "grid_ctf"
_TARGET = "competitor-local"


def _charter(autonomy: str) -> Charter:
    return Charter(
        tier="oss",
        autonomy=autonomy,  # type: ignore[arg-type]
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=[
            CharterTarget(
                name=_TARGET,
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


def _report(mae: float, epoch_id: str, *, domain: str = _SCENARIO) -> CalibrationReport:
    return CalibrationReport(
        domain=domain,
        num_anchors=3,
        alignment=AlignmentResult(mean_absolute_error=mae, bias=0.0, correlation=0.95, num_pairs=3, per_anchor_errors=[]),
        variance=JudgeVarianceResult(mean=0.5, variance=0.0, std_dev=0.0, range=0.0, num_samples=3),
        calibrated=True,
        evaluator_epoch=epoch_id,
        anchor_ids=["a1", "a2", "a3"],
    )


def _reg(tmp_path: Path) -> tuple[EvaluatorEpochRegistry, object, object]:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe(_SCENARIO, e1, now_fn=lambda: "t0")  # active (bootstrap)
    reg.observe(_SCENARIO, e2, now_fn=lambda: "t1")  # candidate
    return reg, e1, e2


_TOL = AlignmentTolerance(domain=_SCENARIO, max_mean_absolute_error=0.12, max_bias=0.08, min_correlation=0.7)


def test_full_autonomy_passing_calibration_activates(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "activated"
    assert reg.active_for(_SCENARIO).epoch_id == e2.epoch_id
    assert reg.load(_SCENARIO, e2.epoch_id).promotion["previous_active"] == e1.epoch_id


def test_full_autonomy_failing_calibration_blocked(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.5, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "blocked"
    assert reg.active_for(_SCENARIO).epoch_id == e1.epoch_id  # unchanged


def test_scenario_differs_from_target_name_no_keyerror(tmp_path: Path) -> None:
    # FIX 1 regression: the operation used to pass ``scenario`` ("grid_ctf") into decide(), which looks
    # the charter target up by name. A valid charter cannot name a target "grid_ctf" (collides with a
    # registered scenario), so the only target is "competitor-local" and the lookup raised
    # ``KeyError: unknown charter target: grid_ctf``. With a separate target_name the promotion succeeds.
    reg, _e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "activated"  # no KeyError


def test_full_autonomy_mismatched_epoch_report_blocked(tmp_path: Path) -> None:
    # FIX 2 regression: a report that PASSES tolerance but is tagged a DIFFERENT epoch than the
    # candidate must not license activation of the uncalibrated candidate.
    reg, e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e1.epoch_id),  # lineage points at e1, not the candidate e2
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "blocked"
    assert reg.active_for(_SCENARIO).epoch_id == e1.epoch_id  # candidate NOT activated


def test_mismatched_report_carries_no_calibration_evidence(tmp_path: Path) -> None:
    # FIX 2: a human reviewer can still override, but the recorded metadata must not carry the
    # unrelated report's calibration deltas/anchors.
    reg, e1, e2 = _reg(tmp_path)
    decision = ReviewerDecision(outcome="approved", reviewed_by="jay", reviewed_at="t3")
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e1.epoch_id, domain="other"),  # wrong epoch AND wrong domain
        tolerance=_TOL,
        charter=_charter("full"),
        reviewer_decision=decision,
        now_fn=lambda: "t3",
    )
    assert out.outcome == "activated"
    promo = reg.load(_SCENARIO, e2.epoch_id).promotion
    assert promo["alignment_delta"] is None
    assert promo["calibration_anchors"] == 0
    assert promo["calibration_anchor_ids"] == []


def test_promotion_metadata_records_anchor_ids(tmp_path: Path) -> None:
    # FIX 5: the metadata records the anchor identities, not only their count.
    reg, _e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "activated"
    promo = reg.load(_SCENARIO, e2.epoch_id).promotion
    assert promo["calibration_anchor_ids"] == ["a1", "a2", "a3"]
    assert promo["calibration_anchors"] == 3


def test_propose_requires_review(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("propose"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "pending_review"
    assert reg.active_for(_SCENARIO).epoch_id == e1.epoch_id
    assert reg.load(_SCENARIO, e2.epoch_id).promotion["requires_review"] is True


def test_reviewer_approved_activates(tmp_path: Path) -> None:
    reg, _e1, e2 = _reg(tmp_path)
    decision = ReviewerDecision(outcome="approved", reviewed_by="jay", reviewed_at="t3")
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("propose"),
        reviewer_decision=decision,
        now_fn=lambda: "t3",
    )
    assert out.outcome == "activated"
    assert reg.load(_SCENARIO, e2.epoch_id).promotion["decision"]["reviewed_by"] == "jay"


def test_reviewer_rejected_stays(tmp_path: Path) -> None:
    reg, e1, e2 = _reg(tmp_path)
    decision = ReviewerDecision(outcome="rejected", reviewed_by="jay", reviewed_at="t3")
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("propose"),
        reviewer_decision=decision,
        now_fn=lambda: "t3",
    )
    assert out.outcome == "rejected"
    assert reg.active_for(_SCENARIO).epoch_id == e1.epoch_id


def test_missing_candidate_noop(tmp_path: Path) -> None:
    reg, _e1, _e2 = _reg(tmp_path)
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        "f" * 64,
        target_name=_TARGET,
        calibration_report=_report(0.05, "f" * 64),
        tolerance=_TOL,
        charter=_charter("full"),
        now_fn=lambda: "t2",
    )
    assert out.outcome == "noop"


def test_activation_clears_quarantine_when_sqlite_provided(tmp_path: Path) -> None:
    reg, _e1, e2 = _reg(tmp_path)
    store = SQLiteStore(tmp_path / "runs.sqlite3")
    store.migrate(_MIGRATIONS)
    store.create_run("run-t1", _SCENARIO, 1, "local", agent_provider="anthropic")
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
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        sqlite=store,
        now_fn=lambda: "t2",
    )
    assert out.outcome == "activated"
    assert store.get_generation_metrics("run-t1")[0]["quarantined"] is None


def test_already_active_reconciles_quarantine_clear(tmp_path: Path) -> None:
    # FIX 4 regression: registry.promote commits activation BEFORE the sqlite clear. If a prior clear
    # raised, the epoch is active but its rows stay quarantined; a retry must reconcile the clear
    # rather than dead-noop on the already-active guard.
    reg, _e1, e2 = _reg(tmp_path)
    reg.promote(_SCENARIO, e2.epoch_id, promotion={"promoted_at": "t2"})  # candidate already active
    store = SQLiteStore(tmp_path / "runs.sqlite3")
    store.migrate(_MIGRATIONS)
    store.create_run("run-t1", _SCENARIO, 1, "local", agent_provider="anthropic")
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
        quarantined=True,  # never cleared by the crashed prior promotion
    )
    out = promote_evaluator_epoch(
        reg,
        _SCENARIO,
        e2.epoch_id,
        target_name=_TARGET,
        calibration_report=_report(0.05, e2.epoch_id),
        tolerance=_TOL,
        charter=_charter("full"),
        sqlite=store,
        now_fn=lambda: "t3",
    )
    assert out.outcome == "activated"  # reconciled, not a dead noop
    assert store.get_generation_metrics("run-t1")[0]["quarantined"] is None
