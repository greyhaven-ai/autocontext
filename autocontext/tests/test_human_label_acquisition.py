"""Budgeted acquisition, reviewer provenance and protected-split isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autocontext.analytics.calibration import (
    AcquisitionCandidate,
    AcquisitionPolicy,
    CalibrationStore,
    ProtectedMembership,
    export_acquisition_labels,
    pending_acquisition_samples,
    reconcile_acquisition_round,
    record_acquisition_review,
    select_acquisition_round,
)
from autocontext.cli import app
from autocontext.context_bundles.models import stable_digest
from autocontext.storage.sqlite_store import SQLiteStore
from autocontext.util.json_io import write_json


def candidate(index: int, *, split: str = "training", group: str | None = None) -> AcquisitionCandidate:
    return AcquisitionCandidate(
        case_id=f"case-{index}", group_id=group or f"group-{index}", scenario="task",
        source_run_id=f"run-{index}", source_trace_id=f"trace-{index}",
        source_trace_digest=f"{index + 1:064x}", evaluator_identity="a" * 64,
        task_prompt=f"Explain case {index}", output_text=f"Answer {index}", rubric="Factual accuracy",
        judge_scores=(0.45, 0.5) if index % 2 else (0.1, 0.95),
        objective_score=0.0 if index == 2 else None, split=split,
        criterion_ids=("accuracy",),
    )


@pytest.fixture
def stores(tmp_path: Path) -> tuple[CalibrationStore, SQLiteStore]:
    db = SQLiteStore(tmp_path / "feedback.db")
    db.migrate(Path(__file__).resolve().parent.parent / "migrations")
    return CalibrationStore(tmp_path / "analytics"), db


def test_targeted_and_random_audit_are_deterministic_and_keep_full_context() -> None:
    cases = [candidate(i) for i in range(10)] + [candidate(10, split="promotion_test")]
    protected = ProtectedMembership(group_ids=("group-10",), content_digests=(cases[-1].content_digest,))
    policy = AcquisitionPolicy(max_labels=6, random_audit_fraction=0.33, seed=73)
    left = select_acquisition_round(cases, policy, protected, round_id="batch-one")
    right = select_acquisition_round(list(reversed(cases)), policy, protected, round_id="batch-one")
    assert [s.sample_id for s in left.samples] == [s.sample_id for s in right.samples]
    assert len(left.samples) == 6
    assert sum(s.metadata["acquisition_reason"] == "random_audit" for s in left.samples) == 2
    assert "group-10" not in {s.metadata["group_id"] for s in left.samples}
    assert all(s.metadata["task_prompt"] and s.metadata["output_text"] and s.metadata["rubric"] for s in left.samples)
    assert all(s.evaluator_epoch == "a" * 64 and s.metadata["sampling_seed"] == 73 for s in left.samples)
    assert left.metadata["protected_membership_digest"] == protected.digest


def test_protected_group_or_duplicate_cross_split_is_rejected() -> None:
    cases = [candidate(1), candidate(2)]
    with pytest.raises(ValueError, match="protected"):
        select_acquisition_round(cases, AcquisitionPolicy(max_labels=2),
                                 ProtectedMembership(group_ids=("group-1",)), round_id="batch")
    with pytest.raises(ValueError, match="split|group"):
        select_acquisition_round([candidate(1), candidate(3, split="development", group="group-1")],
                                 AcquisitionPolicy(max_labels=2), ProtectedMembership(), round_id="batch")
    near_duplicate = candidate(3, split="development").model_copy(update={"task_prompt": "  EXPLAIN   case 1 "})
    with pytest.raises(ValueError, match="split"):
        select_acquisition_round([candidate(1), near_duplicate], AcquisitionPolicy(max_labels=2),
                                 ProtectedMembership(), round_id="batch")
    with pytest.raises(ValueError, match="protected"):
        select_acquisition_round([candidate(1)], AcquisitionPolicy(max_labels=1),
                                 ProtectedMembership(task_digests=(candidate(1).task_digest,)), round_id="batch")


def test_label_skip_correction_and_export_reuse_feedback_store(stores: tuple[CalibrationStore, SQLiteStore]) -> None:
    calibration, sqlite = stores
    rnd = select_acquisition_round([candidate(1), candidate(2, split="development")],
                                   AcquisitionPolicy(max_labels=2, random_audit_fraction=.5, seed=7),
                                   ProtectedMembership(), round_id="review-one")
    calibration.persist_round(rnd)
    training = next(sample for sample in rnd.samples if sample.metadata["split"] == "training")
    development = next(sample for sample in rnd.samples if sample.metadata["split"] == "development")
    labeled = record_acquisition_review(calibration, sqlite, rnd.round_id, training.sample_id, reviewer="human-A",
                                        decision="label", human_score=.3, rationale="Misses facts",
                                        criterion_scores={"accuracy": .2})
    assert record_acquisition_review(calibration, sqlite, rnd.round_id, training.sample_id, reviewer="human-A",
                                     decision="label", human_score=.3, rationale="Misses facts",
                                     criterion_scores={"accuracy": .2}).outcome_id == labeled.outcome_id
    with pytest.raises(ValueError, match="correction"):
        record_acquisition_review(calibration, sqlite, rnd.round_id, training.sample_id, reviewer="human-B",
                                  decision="label", human_score=.8, rationale="disagree")
    record_acquisition_review(calibration, sqlite, rnd.round_id, development.sample_id, reviewer="human-B",
                              decision="skip")
    assert pending_acquisition_samples(calibration, rnd.round_id) == []
    record_acquisition_review(calibration, sqlite, rnd.round_id, training.sample_id, reviewer="human-A",
                              decision="correct", human_score=.7, rationale="Corrected after reread",
                              criterion_scores={"accuracy": .8})
    row = sqlite.get_human_feedback_by_acquisition_id(stable_digest((rnd.round_id, training.sample_id)))
    assert row is not None and row["human_score"] == .7 and row["reviewer"] == "human-A"
    assert sqlite.get_calibration_examples("task") == []
    result = export_acquisition_labels(calibration, sqlite, rnd.round_id)
    assert len(result["training"]) == 1 and result["development"] == []
    assert result["training"][0]["criterion_scores"] == {"accuracy": .8}
    assert result["summary"]["skipped"] == 1
    with pytest.raises(ValueError, match="protected"):
        export_acquisition_labels(calibration, sqlite, rnd.round_id,
                                  protected=ProtectedMembership(task_digests=(training.metadata["task_digest"],)))
    assert len(calibration.load_round(rnd.round_id).outcomes) == 3


def test_disagreement_and_newly_protected_group_fail_closed(stores: tuple[CalibrationStore, SQLiteStore]) -> None:
    calibration, sqlite = stores
    rnd = select_acquisition_round([candidate(1), candidate(2)], AcquisitionPolicy(max_labels=2),
                                   ProtectedMembership(), round_id="review-two")
    calibration.persist_round(rnd)
    first, second = rnd.samples
    with pytest.raises(ValueError, match="protected"):
        record_acquisition_review(calibration, sqlite, rnd.round_id, first.sample_id, reviewer="human-A",
                                  decision="label", human_score=.7,
                                  protected=ProtectedMembership(group_ids=(first.metadata["group_id"],)))
    record_acquisition_review(calibration, sqlite, rnd.round_id, first.sample_id, reviewer="human-A",
                              decision="label", human_score=.7)
    record_acquisition_review(calibration, sqlite, rnd.round_id, first.sample_id, reviewer="human-B",
                              decision="disagree", human_score=.2, rationale="Different interpretation")
    assert [sample.sample_id for sample in pending_acquisition_samples(calibration, rnd.round_id)] == [
        first.sample_id, second.sample_id]
    exported = export_acquisition_labels(calibration, sqlite, rnd.round_id)
    assert exported["training"] == [] and exported["summary"]["unresolved_disagreements"] == 1
    record_acquisition_review(calibration, sqlite, rnd.round_id, first.sample_id, reviewer="human-C",
                              decision="correct", human_score=.6, rationale="Adjudicated")
    assert len(export_acquisition_labels(calibration, sqlite, rnd.round_id)["training"]) == 1


def test_resume_recovers_label_after_feedback_write_failure(
    stores: tuple[CalibrationStore, SQLiteStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibration, sqlite = stores
    rnd = select_acquisition_round([candidate(1)], AcquisitionPolicy(max_labels=1),
                                   ProtectedMembership(), round_id="review-three")
    calibration.persist_round(rnd)
    original = sqlite.record_acquired_feedback
    attempts = [0]

    def interrupted(**kwargs):
        attempts[0] += 1
        if attempts[0] == 1:
            raise OSError("injected failure")
        return original(**kwargs)

    monkeypatch.setattr(sqlite, "record_acquired_feedback", interrupted)
    with pytest.raises(OSError):
        record_acquisition_review(calibration, sqlite, rnd.round_id, rnd.samples[0].sample_id,
                                  reviewer="human-A", decision="label", human_score=.8, rationale="good")
    assert len(calibration.load_round(rnd.round_id).outcomes) == 1
    reconcile_acquisition_round(calibration, sqlite, rnd.round_id)
    retry = record_acquisition_review(calibration, sqlite, rnd.round_id, rnd.samples[0].sample_id,
                                      reviewer="human-A", decision="label", human_score=.8, rationale="good")
    assert len(calibration.load_round(rnd.round_id).outcomes) == 1 and retry.decision == "label"
    assert len(sqlite.get_human_feedback("task")) == 1


def test_cli_select_review_and_export(tmp_path: Path) -> None:
    pool = tmp_path / "pool.json"
    protected = tmp_path / "protected.json"
    root = tmp_path / "analytics"
    db = tmp_path / "feedback.db"
    write_json(pool, [candidate(1).model_dump(mode="json"), candidate(2, split="development").model_dump(mode="json")])
    write_json(protected, ProtectedMembership().model_dump(mode="json"))
    runner = CliRunner()
    selected = runner.invoke(app, ["labels", "select", "--pool", str(pool), "--protected", str(protected),
                                   "--round-id", "cli-round", "--budget", "2", "--root", str(root)])
    assert selected.exit_code == 0, selected.output
    pending = runner.invoke(app, ["labels", "pending", "cli-round", "--root", str(root), "--db-path", str(db)])
    assert pending.exit_code == 0, pending.output
    sample = json.loads(pending.output)[0]
    assert sample["metadata"]["task_prompt"] and sample["metadata"]["source_trace_id"]
    labeled = runner.invoke(app, ["labels", "review", "cli-round", sample["sample_id"],
                                  "--protected", str(protected), "--by", "human-reviewer", "--decision", "label",
                                  "--score", "0.8", "--rationale", "Checked output", "--root", str(root),
                                  "--db-path", str(db)])
    assert labeled.exit_code == 0, labeled.output
    exported = runner.invoke(app, ["labels", "export", "cli-round", "--protected", str(protected),
                                   "--root", str(root), "--db-path", str(db)])
    assert exported.exit_code == 0, exported.output
    data = json.loads(exported.output)
    assert sum(len(data[split]) for split in ("training", "development")) == 1
    assert data[sample["metadata"]["split"]][0]["reviewer"] == "human-reviewer"
