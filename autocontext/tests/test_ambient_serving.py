"""tests for per-target serving resolution, the health supervisor, and the slot-aliasing fix."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autocontext.ambient.charter import Charter, CharterAnchor, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.promote import PromoteStage
from autocontext.ambient.publish import publish_candidate
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.serving import ServerSupervisor, resolve_active_serving
from autocontext.ambient.stage import StageContext
from autocontext.ambient.training_backend import TrainOutcome
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.training.model_registry import DistilledModelRecord, ModelRegistry

_NOW = "2026-07-06T12:00:00+00:00"
_ANCHOR = "claude-sonnet-5"


def _target(name: str, **overrides: Any) -> CharterTarget:
    base: dict[str, Any] = {
        "name": name,
        "kind": "role",
        "selector": "competitor",
        "base_model": "tiny",
        "min_dataset_records": 1,
        "eval_suite": "anchor-v1",
    }
    base.update(overrides)
    return CharterTarget(**base)


def _charter(targets: list[CharterTarget], autonomy: str = "full") -> Charter:
    return Charter(
        tier="oss",
        autonomy=autonomy,  # type: ignore[arg-type]
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=targets,
        budgets=CharterBudgets(gpu_hours_per_window=10.0, window_hours=24, disk_quota_gb=1.0),
        anchor=CharterAnchor(provider="anthropic", model=_ANCHOR, rubric="Score 0 to 1."),
    )


def _ctx(tmp_path: Path, charter: Charter) -> StageContext:
    return StageContext(
        charter=charter,
        queue=AmbientQueue(tmp_path / "q.sqlite3"),
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
    )


def _eval(score: float, *, anchor: str = _ANCHOR, drift_ok: bool = True) -> dict[str, Any]:
    return {
        "anchor_model": anchor,
        "score": score,
        "drift_magnitude": 0.1,
        "drift_ok": drift_ok,
        "evaluated_at": _NOW,
    }


def _active_record(registry: ModelRegistry, target_name: str, *, backend: str = "mlx") -> DistilledModelRecord:
    record = DistilledModelRecord(
        artifact_id=f"active-{target_name}",
        scenario=target_name,
        scenario_family="grid_ctf",
        backend=backend,
        checkpoint_path=f"/ckpt/{target_name}",
        runtime_types=["provider"],
        activation_state="active",
        training_metrics={},
        provenance={},
        metadata={"target": target_name, "base_model": "tiny"},
    )
    registry.register(record)
    return record


# ---------------------------------------------------------------------------
# resolve_active_serving
# ---------------------------------------------------------------------------


def test_resolve_active_serving_returns_the_active_record(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    active = _active_record(registry, "competitor-grid_ctf")

    decision = resolve_active_serving(registry, "competitor-grid_ctf", "mlx")

    assert decision.source == "registry"
    assert decision.fallback_used is False
    assert decision.artifact_id == active.artifact_id
    assert decision.model == active.checkpoint_path


def test_resolve_active_serving_falls_back_when_no_active_model(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")

    decision = resolve_active_serving(registry, "analyst-grid_ctf", "mlx")

    assert decision.fallback_used is True
    assert decision.source == "fallback"
    assert decision.artifact_id is None


# ---------------------------------------------------------------------------
# ServerSupervisor.wait_until_healthy
# ---------------------------------------------------------------------------


def test_wait_until_healthy_returns_true_when_it_becomes_healthy() -> None:
    calls = {"n": 0}

    def health_fn() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3  # healthy on the third poll

    intervals: list[int] = []
    supervisor = ServerSupervisor(
        health_fn=health_fn,
        poll_attempts=5,
        poll_interval_fn=intervals.append,
    )

    assert supervisor.wait_until_healthy() is True
    assert calls["n"] == 3
    assert intervals == [0, 1]  # poll_interval_fn is invoked between the two failed polls


def test_wait_until_healthy_returns_false_when_never_healthy() -> None:
    intervals: list[int] = []
    supervisor = ServerSupervisor(
        health_fn=lambda: False,
        poll_attempts=4,
        poll_interval_fn=intervals.append,
    )

    assert supervisor.wait_until_healthy() is False
    assert intervals == [0, 1, 2]  # called between attempts, never after the last


def test_wait_until_healthy_defaults_to_no_sleep() -> None:
    supervisor = ServerSupervisor(health_fn=lambda: True)

    assert supervisor.wait_until_healthy() is True


# ---------------------------------------------------------------------------
# ServerSupervisor.rollback
# ---------------------------------------------------------------------------


def test_rollback_reactivates_the_previous_model(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _active_record(registry, "competitor-grid_ctf")
    previous = registry.load("active-competitor-grid_ctf")
    assert previous is not None
    registry.deactivate(previous.artifact_id)  # simulate a bad promote having disabled it

    supervisor = ServerSupervisor(health_fn=lambda: False)
    supervisor.rollback(registry, "competitor-grid_ctf", previous.artifact_id)

    restored = registry.load(previous.artifact_id)
    assert restored is not None
    assert restored.activation_state == "active"


def test_rollback_with_no_previous_artifact_raises(tmp_path: Path) -> None:
    supervisor = ServerSupervisor(health_fn=lambda: True)

    with pytest.raises(ValueError):
        supervisor.rollback(ModelRegistry(tmp_path / "registry"), "competitor-grid_ctf", "")


# ---------------------------------------------------------------------------
# regression: the slot-vs-target aliasing bug (FIX A)
# ---------------------------------------------------------------------------


def _publish(registry: ModelRegistry, tmp_path: Path, target: CharterTarget) -> str:
    outcome = TrainOutcome(
        checkpoint_path=tmp_path / target.name / "adapters",
        backend="mlx",
        metrics={"avg_score": 0.8, "valid_rate": 1.0, "num_records": 6.0},
        gpu_hours=1.0,
    )
    return publish_candidate(
        outcome=outcome,
        target=target,
        scenario="grid_ctf",  # both targets share the real scenario
        registry=registry,
        artifacts_root=tmp_path / "artifacts",
        run_id=f"ambient-{target.name}-6",
        record_count=6,
    )


def test_publish_slots_candidates_by_target_not_by_scenario(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    comp_id = _publish(registry, tmp_path, _target("competitor-grid_ctf"))

    comp = registry.load(comp_id)
    assert comp is not None
    # the registry SLOT is the target name; the real scenario is preserved as scenario_family.
    assert comp.scenario == "competitor-grid_ctf"
    assert comp.scenario_family == "grid_ctf"


def test_promoting_a_sibling_target_does_not_demote_the_live_model(tmp_path: Path) -> None:
    # two charter targets that both map to the same real scenario ("grid_ctf") but are
    # trained and served independently. before the fix they shared one registry slot, so
    # promoting one demoted the other's live model and lost its rollback pointer.
    registry = ModelRegistry(tmp_path / "registry")
    comp = _target("competitor-grid_ctf")
    analyst = _target("analyst-grid_ctf")
    comp_id = _publish(registry, tmp_path, comp)
    analyst_id = _publish(registry, tmp_path, analyst)

    # make the competitor model the live binding, and give both candidates a clean eval so the
    # promote flow can act on the analyst candidate.
    comp_rec = registry.load(comp_id)
    assert comp_rec is not None
    comp_rec.metadata["eval"] = _eval(0.8)
    registry.register(comp_rec)
    registry.activate(comp_id)

    analyst_rec = registry.load(analyst_id)
    assert analyst_rec is not None
    analyst_rec.metadata["eval"] = _eval(0.9)
    registry.register(analyst_rec)

    stage = PromoteStage(name="promote", registry=registry, now_fn=lambda: _NOW)
    result = stage.run_once(_ctx(tmp_path, _charter([comp, analyst])))

    assert (result.processed, result.errors) == (1, 0)
    # the analyst candidate is now active for its own slot...
    promoted_analyst = registry.load(analyst_id)
    assert promoted_analyst is not None and promoted_analyst.activation_state == "active"
    # ...and the competitor's live model is UNTOUCHED (not cross-demoted).
    still_live = registry.load(comp_id)
    assert still_live is not None and still_live.activation_state == "active"
