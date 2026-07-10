from __future__ import annotations

from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry


def _epoch(rubric: str) -> object:
    return compute_evaluator_epoch(rubric, "anthropic", "claude-sonnet-4-5")


def test_first_epoch_bootstraps_active(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1 = _epoch("rubric one")
    rec = reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    assert rec.activation_state == "active"
    assert reg.active_for("grid_ctf").epoch_id == e1.epoch_id


def test_same_epoch_is_noop(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1 = _epoch("rubric one")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    rec = reg.observe("grid_ctf", e1, now_fn=lambda: "t1")
    assert rec.activation_state == "active"
    assert len(reg.list_for_scenario("grid_ctf")) == 1


def test_new_epoch_mints_candidate(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1, e2 = _epoch("rubric one"), _epoch("rubric two")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    rec = reg.observe("grid_ctf", e2, now_fn=lambda: "t1")
    assert rec.activation_state == "candidate"
    assert reg.active_for("grid_ctf").epoch_id == e1.epoch_id  # active unchanged


def test_activate_demotes_prior(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1, e2 = _epoch("rubric one"), _epoch("rubric two")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.observe("grid_ctf", e2, now_fn=lambda: "t1")
    reg.activate("grid_ctf", e2.epoch_id)
    assert reg.active_for("grid_ctf").epoch_id == e2.epoch_id
    assert reg.load("grid_ctf", e1.epoch_id).activation_state == "disabled"


def test_reappearing_disabled_epoch_is_candidate_not_bootstrap(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1, e2 = _epoch("rubric one"), _epoch("rubric two")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.observe("grid_ctf", e2, now_fn=lambda: "t1")
    reg.activate("grid_ctf", e2.epoch_id)  # e1 now disabled
    rec = reg.observe("grid_ctf", e1, now_fn=lambda: "t2")  # e1 seen again
    assert rec.activation_state == "disabled"  # known record, not re-bootstrapped


def test_activate_missing_epoch_leaves_prior_active(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1 = _epoch("rubric one")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.activate("grid_ctf", "does-not-exist")  # bad id must not strand the scenario
    active = reg.active_for("grid_ctf")
    assert active is not None
    assert active.epoch_id == e1.epoch_id
    assert reg.load("grid_ctf", e1.epoch_id).activation_state == "active"


def test_observe_id_first_epoch_bootstraps_active(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    rec = reg.observe_id("grid_ctf", "e-1", now_fn=lambda: "t0")
    assert rec.activation_state == "active"
    assert reg.active_for("grid_ctf").epoch_id == "e-1"
    # only the id is known at this call site; components backfill in C2
    assert rec.rubric_hash == ""
    assert rec.judge_provider == ""
    assert rec.judge_model == ""


def test_observe_id_new_id_mints_candidate(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    reg.observe_id("grid_ctf", "e-1", now_fn=lambda: "t0")
    rec = reg.observe_id("grid_ctf", "e-2", now_fn=lambda: "t1")
    assert rec.activation_state == "candidate"
    assert reg.active_for("grid_ctf").epoch_id == "e-1"  # active unchanged


def test_observe_id_same_id_is_noop(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    reg.observe_id("grid_ctf", "e-1", now_fn=lambda: "t0")
    rec = reg.observe_id("grid_ctf", "e-1", now_fn=lambda: "t1")
    assert rec.activation_state == "active"
    assert len(reg.list_for_scenario("grid_ctf")) == 1


def test_prefix_sharing_scenarios_do_not_leak(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1, e2 = _epoch("rubric one"), _epoch("rubric two")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.observe("grid_ctf_", e2, now_fn=lambda: "t1")
    assert {r.epoch_id for r in reg.list_for_scenario("grid_ctf")} == {e1.epoch_id}
    assert {r.epoch_id for r in reg.list_for_scenario("grid_ctf_")} == {e2.epoch_id}
    assert reg.active_for("grid_ctf").epoch_id == e1.epoch_id
    assert reg.active_for("grid_ctf_").epoch_id == e2.epoch_id
    # each scenario bootstrapped its own first epoch to active independently
    assert reg.observe("grid_ctf_", e2, now_fn=lambda: "t2").activation_state == "active"


def test_observe_epoch_quarantined_degrades_on_registry_io_failure(tmp_path) -> None:
    from autocontext.execution.evaluator_epoch_registry import observe_epoch_quarantined

    # None epoch is never observed.
    assert observe_epoch_quarantined(tmp_path / "reg", "grid_ctf", None) is None
    # A root that is an existing FILE makes the registry mkdir fail; the marker must degrade to
    # None (score persistence must never abort on a non-critical lineage failure), not raise.
    bad_root = tmp_path / "afile"
    bad_root.write_text("x")
    assert observe_epoch_quarantined(bad_root, "grid_ctf", "e-1") is None
