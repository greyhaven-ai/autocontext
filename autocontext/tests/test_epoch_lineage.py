"""Stale-epoch lineage classification + annotation (AC-885 Slice D1)."""

from __future__ import annotations

from pathlib import Path

from autocontext.execution.epoch_lineage import annotate_status_rows
from autocontext.execution.evaluator_epoch import classify_epoch_lineage, compute_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry


def test_classify_four_states() -> None:
    assert classify_epoch_lineage("e1", None) == "no_active_epoch"
    assert classify_epoch_lineage(None, None) == "no_active_epoch"
    assert classify_epoch_lineage(None, "e2") == "unknown"
    assert classify_epoch_lineage("e2", "e2") == "current"
    assert classify_epoch_lineage("e1", "e2") == "stale"


def test_annotate_reads_active_and_classifies(tmp_path: Path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path / "_evaluator_epochs")
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")  # active (bootstrap)
    reg.observe("grid_ctf", e2, now_fn=lambda: "t1")  # candidate
    rows = [
        {"generation_index": 1, "evaluator_epoch": e1.epoch_id},
        {"generation_index": 2, "evaluator_epoch": None},
    ]
    out, active = annotate_status_rows(rows, "grid_ctf", reg)
    assert active == e1.epoch_id
    assert out[0]["evaluator_epoch_status"] == "current"
    assert out[1]["evaluator_epoch_status"] == "unknown"
    # inputs not mutated
    assert "evaluator_epoch_status" not in rows[0]


def test_annotate_stale_after_promotion(tmp_path: Path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path / "_evaluator_epochs")
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.observe("grid_ctf", e2, now_fn=lambda: "t1")
    reg.activate("grid_ctf", e2.epoch_id)  # promote e2 -> e1 rows now stale
    rows = [{"generation_index": 1, "evaluator_epoch": e1.epoch_id}]
    out, active = annotate_status_rows(rows, "grid_ctf", reg)
    assert active == e2.epoch_id
    assert out[0]["evaluator_epoch_status"] == "stale"


def test_annotate_no_scenario(tmp_path: Path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path / "_evaluator_epochs")
    rows = [{"generation_index": 1, "evaluator_epoch": "e1"}]
    out, active = annotate_status_rows(rows, None, reg)
    assert active is None
    assert out[0]["evaluator_epoch_status"] == "no_active_epoch"
