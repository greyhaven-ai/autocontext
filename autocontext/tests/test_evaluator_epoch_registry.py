from __future__ import annotations

import hashlib

from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import (
    EvaluatorEpochRecord,
    EvaluatorEpochRegistry,
)


def _epoch(rubric: str) -> object:
    return compute_evaluator_epoch(rubric, "anthropic", "claude-sonnet-4-5")


def _hex_id(seed: str) -> str:
    """A realistic 64-char sha256-hex epoch id (production ids are sha256 digests)."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


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


def test_reappearing_disabled_epoch_stays_disabled(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    e1, e2 = _epoch("rubric one"), _epoch("rubric two")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.observe("grid_ctf", e2, now_fn=lambda: "t1")
    reg.activate("grid_ctf", e2.epoch_id)  # e1 now disabled
    rec = reg.observe("grid_ctf", e1, now_fn=lambda: "t2")  # e1 seen again
    assert rec.activation_state == "disabled"  # known record returned unchanged, not re-bootstrapped


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
    id1 = _hex_id("e-1")
    rec = reg.observe_id("grid_ctf", id1, now_fn=lambda: "t0")
    assert rec.activation_state == "active"
    assert reg.active_for("grid_ctf").epoch_id == id1
    # only the id is known at this call site; components backfill in C2
    assert rec.rubric_hash == ""
    assert rec.judge_provider == ""
    assert rec.judge_model == ""


def test_observe_id_new_id_mints_candidate(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    id1, id2 = _hex_id("e-1"), _hex_id("e-2")
    reg.observe_id("grid_ctf", id1, now_fn=lambda: "t0")
    rec = reg.observe_id("grid_ctf", id2, now_fn=lambda: "t1")
    assert rec.activation_state == "candidate"
    assert reg.active_for("grid_ctf").epoch_id == id1  # active unchanged


def test_observe_id_same_id_is_noop(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    id1 = _hex_id("e-1")
    reg.observe_id("grid_ctf", id1, now_fn=lambda: "t0")
    rec = reg.observe_id("grid_ctf", id1, now_fn=lambda: "t1")
    assert rec.activation_state == "active"
    assert len(reg.list_for_scenario("grid_ctf")) == 1


def test_observe_id_rejects_non_hex_id(tmp_path) -> None:
    import pytest

    reg = EvaluatorEpochRegistry(tmp_path)
    with pytest.raises(ValueError):
        reg.observe_id("grid_ctf", "e-1", now_fn=lambda: "t0")


def test_observe_id_path_traversal_is_rejected_and_writes_nothing(tmp_path) -> None:
    import pytest

    root = tmp_path / "reg"
    reg = EvaluatorEpochRegistry(root)
    with pytest.raises(ValueError):
        reg.observe_id("grid_ctf", "../../escaped", now_fn=lambda: "t0")
    # nothing escaped the registry root
    assert not (tmp_path / "escaped.json").exists()
    assert not (root.parent / "escaped.json").exists()
    assert list(reg.list_for_scenario("grid_ctf")) == []


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


def test_observe_epoch_quarantined_fails_closed_on_registry_io_failure(tmp_path) -> None:
    from autocontext.execution.evaluator_epoch_registry import observe_epoch_quarantined

    # None epoch is genuinely nothing to observe -> None (never quarantined).
    assert observe_epoch_quarantined(tmp_path / "reg", "grid_ctf", None) is None
    # A root that is an existing FILE makes the registry mkdir fail. A non-null epoch whose lifecycle
    # state cannot be verified must fail CLOSED (quarantined=True), never trusted, and never abort
    # score persistence by raising.
    bad_root = tmp_path / "afile"
    bad_root.write_text("x")
    assert observe_epoch_quarantined(bad_root, "grid_ctf", _hex_id("e-1")) is True
    # An invalid (non-hex) id is likewise unverifiable -> quarantined, not trusted.
    assert observe_epoch_quarantined(tmp_path / "reg", "grid_ctf", "../../escaped") is True


def test_active_for_self_heals_multiple_active(tmp_path) -> None:
    reg = EvaluatorEpochRegistry(tmp_path)
    id_a, id_b = _hex_id("aaa"), _hex_id("bbb")
    smaller, larger = sorted([id_a, id_b])
    # Manually plant a corrupt two-active state (as a legacy/raced write would leave).
    for epoch_id in (id_a, id_b):
        reg.register(
            EvaluatorEpochRecord(
                scenario="grid_ctf",
                epoch_id=epoch_id,
                rubric_hash="",
                judge_provider="",
                judge_model="",
                activation_state="active",
                created_at="t0",
            )
        )
    kept = reg.active_for("grid_ctf")
    assert kept is not None
    assert kept.epoch_id == smaller  # deterministically keep the lexicographically smallest
    # exactly one active remains; the other is demoted to disabled (demote-not-delete)
    actives = [r for r in reg.list_for_scenario("grid_ctf") if r.activation_state == "active"]
    assert len(actives) == 1
    assert reg.load("grid_ctf", larger).activation_state == "disabled"


def test_promote_activates_and_stamps_metadata(tmp_path) -> None:
    from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
    from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry

    reg = EvaluatorEpochRegistry(tmp_path)
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")  # active bootstrap
    reg.observe("grid_ctf", e2, now_fn=lambda: "t1")  # candidate
    meta = {"promoted_at": "t2", "previous_active": e1.epoch_id, "requires_review": False}
    reg.promote("grid_ctf", e2.epoch_id, promotion=meta)
    assert reg.active_for("grid_ctf").epoch_id == e2.epoch_id
    assert reg.load("grid_ctf", e1.epoch_id).activation_state == "disabled"
    assert reg.load("grid_ctf", e2.epoch_id).promotion == meta


def test_promote_missing_epoch_is_noop(tmp_path) -> None:
    from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
    from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry

    reg = EvaluatorEpochRegistry(tmp_path)
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.promote("grid_ctf", "f" * 64, promotion={"x": 1})  # nonexistent id
    assert reg.active_for("grid_ctf").epoch_id == e1.epoch_id  # unchanged


def test_concurrent_first_observe_yields_single_active(tmp_path) -> None:
    import concurrent.futures

    root = tmp_path / "reg"
    EvaluatorEpochRegistry(root)  # create the root so the per-scenario lock file has a home
    ids = [_hex_id(f"epoch-{i}") for i in range(8)]

    def _first_observe(epoch_id: str) -> str:
        reg = EvaluatorEpochRegistry(root)  # a fresh registry per worker (separate process-like)
        return reg.observe_id("grid_ctf", epoch_id).activation_state

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ids)) as pool:
        list(pool.map(_first_observe, ids))

    reg = EvaluatorEpochRegistry(root)
    actives = [r for r in reg.list_for_scenario("grid_ctf") if r.activation_state == "active"]
    assert len(actives) == 1  # exactly one bootstrap won under the per-scenario lock
