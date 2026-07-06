"""tests for the evaluate stage: scoring candidates under the frozen anchor + drift canary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.ambient.charter import Charter, CharterAnchor, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.evaluate import EvaluateStage
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.stage import StageContext
from autocontext.execution.bias_probes import BiasProbeResult
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.training.model_registry import DistilledModelRecord, ModelRegistry

_NOW = "2026-07-06T12:00:00+00:00"


def _target(name: str, eval_suite: str) -> CharterTarget:
    return CharterTarget(
        name=name,
        kind="role",
        selector="competitor",
        base_model="tiny",
        min_dataset_records=5,
        eval_suite=eval_suite,
    )


def _charter(targets: list[CharterTarget]) -> Charter:
    return Charter(
        tier="oss",
        autonomy="train",
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=targets,
        budgets=CharterBudgets(gpu_hours_per_window=10.0, window_hours=24, disk_quota_gb=1.0),
        anchor=CharterAnchor(provider="anthropic", model="claude-sonnet-5", rubric="Score 0 to 1."),
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


def _seed_suite(suites_dir: Path, name: str, cases: list[tuple[str, str]]) -> None:
    suites_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"prompt": prompt, "reference": reference}) for prompt, reference in cases]
    (suites_dir / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _candidate(registry: ModelRegistry, artifact_id: str, target: str, **overrides: Any) -> DistilledModelRecord:
    metadata: dict[str, Any] = {"target": target}
    metadata.update(overrides.pop("metadata", {}))
    record = DistilledModelRecord(
        artifact_id=artifact_id,
        scenario="grid_ctf",
        scenario_family="",
        backend="mlx",
        checkpoint_path=f"/ckpt/{artifact_id}",
        runtime_types=["provider"],
        activation_state=overrides.pop("activation_state", "candidate"),
        training_metrics={},
        provenance={},
        metadata=metadata,
    )
    registry.register(record)
    return record


class _FixedScorer:
    def __init__(self, value: float) -> None:
        self.value = value

    def score(self, prompt: str, output: str) -> float:
        return self.value


class _MapScorer:
    def __init__(self, mapping: dict[str, float]) -> None:
        self.mapping = mapping

    def score(self, prompt: str, output: str) -> float:
        return self.mapping[prompt]


class _RaisingScorer:
    def score(self, prompt: str, output: str) -> float:
        if prompt == "boom":
            raise RuntimeError("scorer exploded")
        return 0.7


def _probe(magnitude: float) -> Any:
    def _fn(anchor: CharterAnchor) -> BiasProbeResult:
        return BiasProbeResult(probe_type="position", detected=magnitude > 0.1, magnitude=magnitude, details="")

    return _fn


def _stage(tmp_path: Path, registry: ModelRegistry, scorer: Any, magnitude: float, drift_tolerance: float = 0.2) -> EvaluateStage:
    return EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: scorer,
        probe_fn=_probe(magnitude),
        drift_tolerance=drift_tolerance,
        now_fn=lambda: _NOW,
    )


def test_candidate_with_suite_gets_averaged_eval(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1"), ("p2", "r2")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _MapScorer({"p1": 0.6, "p2": 1.0}), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    eval_meta = reloaded.metadata["eval"]
    assert eval_meta["score"] == 0.8
    assert eval_meta["anchor_model"] == "claude-sonnet-5"
    assert eval_meta["drift_magnitude"] == 0.1
    assert eval_meta["drift_ok"] is True
    assert eval_meta["evaluated_at"] == _NOW
    completed = [e for e in _events(tmp_path) if e["event"] == "evaluate_completed"]
    assert completed[0]["payload"] == {"artifact_id": "cand-1", "target": "competitor-local", "score": 0.8, "drift_ok": True}


def test_candidate_without_suite_emits_no_suite_and_is_not_evaluated(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    charter = _charter([_target("competitor-local", "missing-suite")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert "eval" not in reloaded.metadata
    no_suite = [e for e in _events(tmp_path) if e["event"] == "evaluate_no_suite"]
    assert no_suite[0]["payload"] == {"artifact_id": "cand-1", "target": "competitor-local", "eval_suite": "missing-suite"}


def test_empty_suite_emits_no_suite(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    _seed_suite(tmp_path / "suites", "anchor-v1", [])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None and "eval" not in reloaded.metadata
    assert any(e["event"] == "evaluate_no_suite" for e in _events(tmp_path))


def test_already_evaluated_candidate_is_skipped(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local", metadata={"eval": {"score": 0.5}})
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert reloaded.metadata["eval"] == {"score": 0.5}  # untouched
    assert _events(tmp_path) == []


def test_non_candidate_records_are_ignored(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "active-1", "competitor-local", activation_state="active")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("active-1")
    assert reloaded is not None and "eval" not in reloaded.metadata


def test_candidate_with_unknown_target_is_skipped_silently(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "not-in-charter")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None and "eval" not in reloaded.metadata
    assert _events(tmp_path) == []


def test_scorer_exception_is_isolated_per_candidate(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-fail", "target-a")
    _candidate(registry, "cand-ok", "target-b")
    _seed_suite(tmp_path / "suites", "suite-a", [("boom", "r")])
    _seed_suite(tmp_path / "suites", "suite-b", [("fine", "r")])
    charter = _charter([_target("target-a", "suite-a"), _target("target-b", "suite-b")])
    stage = _stage(tmp_path, registry, _RaisingScorer(), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 1)
    failed = registry.load("cand-fail")
    ok = registry.load("cand-ok")
    assert failed is not None and "eval" not in failed.metadata
    assert ok is not None and ok.metadata["eval"]["score"] == 0.7
    failures = [e for e in _events(tmp_path) if e["event"] == "evaluate_candidate_failed"]
    assert failures[0]["payload"]["artifact_id"] == "cand-fail"
    assert "scorer exploded" in failures[0]["payload"]["error"]


def test_drift_above_tolerance_records_eval_with_drift_ok_false(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.5, drift_tolerance=0.2)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert reloaded.metadata["eval"]["drift_ok"] is False
    assert reloaded.metadata["eval"]["drift_magnitude"] == 0.5


def test_reregister_keeps_activation_state_candidate(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    stage.run_once(_ctx(tmp_path, charter))

    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert reloaded.activation_state == "candidate"
