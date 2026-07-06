"""tests for the promote stage: anchor-winning, drift-clean candidates become the live binding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.ambient.charter import Charter, CharterAnchor, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.promote import PromoteStage
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.stage import StageContext
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.training.model_registry import DistilledModelRecord, ModelRegistry

_NOW = "2026-07-06T12:00:00+00:00"
_ANCHOR = "claude-sonnet-5"


def _target(name: str) -> CharterTarget:
    return CharterTarget(
        name=name,
        kind="role",
        selector="competitor",
        base_model="tiny",
        min_dataset_records=5,
        eval_suite="anchor-v1",
    )


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


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _eval(score: float, *, anchor: str = _ANCHOR, drift_ok: bool = True) -> dict[str, Any]:
    return {
        "anchor_model": anchor,
        "score": score,
        "drift_magnitude": 0.1,
        "drift_ok": drift_ok,
        "evaluated_at": _NOW,
    }


def _record(
    registry: ModelRegistry,
    artifact_id: str,
    target: str,
    *,
    state: str,
    eval_meta: dict[str, Any] | None = None,
) -> DistilledModelRecord:
    metadata: dict[str, Any] = {"target": target}
    if eval_meta is not None:
        metadata["eval"] = eval_meta
    record = DistilledModelRecord(
        artifact_id=artifact_id,
        scenario="grid_ctf",
        scenario_family="",
        backend="mlx",
        checkpoint_path=f"/ckpt/{artifact_id}",
        runtime_types=["provider"],
        activation_state=state,
        training_metrics={},
        provenance={},
        metadata=metadata,
    )
    registry.register(record)
    return record


def _stage(registry: ModelRegistry) -> PromoteStage:
    return PromoteStage(name="promote", registry=registry, now_fn=lambda: _NOW)


class _ActivateFailRegistry(ModelRegistry):
    """A registry whose activate() explodes for one poisoned artifact id."""

    def __init__(self, root: Path, fail_id: str) -> None:
        super().__init__(root)
        self.fail_id = fail_id

    def activate(self, artifact_id: str) -> None:
        if artifact_id == self.fail_id:
            raise RuntimeError("activate boom")
        super().activate(artifact_id)


def test_no_incumbent_drift_clean_candidate_is_promoted(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _record(registry, "cand-1", "competitor-local", state="candidate", eval_meta=_eval(0.8))
    charter = _charter([_target("competitor-local")])

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    promoted = registry.load("cand-1")
    assert promoted is not None
    assert promoted.activation_state == "active"
    assert promoted.metadata["probation"] == {"promoted_at": _NOW, "previous_active": ""}
    activated = [e for e in _events(tmp_path) if e["event"] == "promote_activated"]
    assert activated[0]["payload"] == {
        "target": "competitor-local",
        "artifact_id": "cand-1",
        "previous_active": "",
        "score": 0.8,
    }


def test_beats_same_anchor_incumbent_is_promoted(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _record(registry, "old-active", "competitor-local", state="active", eval_meta=_eval(0.6))
    _record(registry, "cand-1", "competitor-local", state="candidate", eval_meta=_eval(0.9))
    charter = _charter([_target("competitor-local")])

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    promoted = registry.load("cand-1")
    incumbent = registry.load("old-active")
    assert promoted is not None and promoted.activation_state == "active"
    assert promoted.metadata["probation"]["previous_active"] == "old-active"
    assert incumbent is not None and incumbent.activation_state == "disabled"
    activated = [e for e in _events(tmp_path) if e["event"] == "promote_activated"]
    assert activated[0]["payload"]["previous_active"] == "old-active"


def test_does_not_beat_incumbent_is_not_promoted(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _record(registry, "old-active", "competitor-local", state="active", eval_meta=_eval(0.9))
    _record(registry, "cand-1", "competitor-local", state="candidate", eval_meta=_eval(0.5))
    charter = _charter([_target("competitor-local")])

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    incumbent = registry.load("old-active")
    candidate = registry.load("cand-1")
    assert incumbent is not None and incumbent.activation_state == "active"
    assert candidate is not None and candidate.activation_state == "candidate"
    assert "probation" not in candidate.metadata
    assert _events(tmp_path) == []


def test_drift_flagged_candidate_is_never_promoted_even_with_higher_score(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _record(registry, "old-active", "competitor-local", state="active", eval_meta=_eval(0.5))
    _record(registry, "cand-1", "competitor-local", state="candidate", eval_meta=_eval(0.99, drift_ok=False))
    charter = _charter([_target("competitor-local")])

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    incumbent = registry.load("old-active")
    candidate = registry.load("cand-1")
    assert incumbent is not None and incumbent.activation_state == "active"
    assert candidate is not None and candidate.activation_state == "candidate"
    assert _events(tmp_path) == []


def test_different_anchor_incumbent_emits_mismatch_and_does_not_activate(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _record(registry, "old-active", "competitor-local", state="active", eval_meta=_eval(0.6, anchor="claude-opus-4-8"))
    _record(registry, "cand-1", "competitor-local", state="candidate", eval_meta=_eval(0.9))
    charter = _charter([_target("competitor-local")])

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    candidate = registry.load("cand-1")
    incumbent = registry.load("old-active")
    assert candidate is not None and candidate.activation_state == "candidate"
    assert incumbent is not None and incumbent.activation_state == "active"
    mismatch = [e for e in _events(tmp_path) if e["event"] == "promote_anchor_mismatch"]
    assert mismatch[0]["payload"] == {"target": "competitor-local", "artifact_id": "cand-1"}


def test_propose_autonomy_requires_approval_and_does_not_activate(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _record(registry, "cand-1", "competitor-local", state="candidate", eval_meta=_eval(0.8))
    charter = _charter([_target("competitor-local")], autonomy="propose")

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    candidate = registry.load("cand-1")
    assert candidate is not None and candidate.activation_state == "candidate"
    assert "probation" not in candidate.metadata
    approval = [e for e in _events(tmp_path) if e["event"] == "promote_requires_approval"]
    assert approval[0]["payload"]["target"] == "competitor-local"
    assert approval[0]["payload"]["artifact_id"] == "cand-1"
    assert "approval" in approval[0]["payload"]["reason"]


def test_train_autonomy_requires_approval_and_does_not_activate(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _record(registry, "cand-1", "competitor-local", state="candidate", eval_meta=_eval(0.8))
    charter = _charter([_target("competitor-local")], autonomy="train")

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    candidate = registry.load("cand-1")
    assert candidate is not None and candidate.activation_state == "candidate"
    assert "probation" not in candidate.metadata
    approval = [e for e in _events(tmp_path) if e["event"] == "promote_requires_approval"]
    assert approval[0]["payload"]["target"] == "competitor-local"
    assert approval[0]["payload"]["artifact_id"] == "cand-1"
    assert "approval" in approval[0]["payload"]["reason"]


def test_equal_score_tie_does_not_promote(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _record(registry, "old-active", "competitor-local", state="active", eval_meta=_eval(0.7))
    _record(registry, "cand-1", "competitor-local", state="candidate", eval_meta=_eval(0.7))
    charter = _charter([_target("competitor-local")])

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    incumbent = registry.load("old-active")
    candidate = registry.load("cand-1")
    assert incumbent is not None and incumbent.activation_state == "active"
    assert candidate is not None and candidate.activation_state == "candidate"
    assert "probation" not in candidate.metadata
    assert _events(tmp_path) == []


def test_per_target_failure_is_isolated(tmp_path: Path) -> None:
    registry = _ActivateFailRegistry(tmp_path / "registry", fail_id="poison")
    _record(registry, "poison", "target-a", state="candidate", eval_meta=_eval(0.8))
    _record(registry, "cand-b", "target-b", state="candidate", eval_meta=_eval(0.8))
    charter = _charter([_target("target-a"), _target("target-b")])

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 1)
    good = registry.load("cand-b")
    assert good is not None and good.activation_state == "active"
    failed = [e for e in _events(tmp_path) if e["event"] == "promote_target_failed"]
    assert failed[0]["payload"]["target"] == "target-a"
    assert "activate boom" in failed[0]["payload"]["error"]


def test_no_candidates_is_quiet(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    charter = _charter([_target("competitor-local")])

    result = _stage(registry).run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    assert _events(tmp_path) == []
