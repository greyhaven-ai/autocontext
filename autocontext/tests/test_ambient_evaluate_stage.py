"""tests for the evaluate stage: scoring candidates under the frozen anchor + drift canary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.ambient.charter import Charter, CharterAnchor, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.evaluate import EvaluateStage, eval_fingerprint
from autocontext.ambient.promote import PromoteStage
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


class _CountingProbe:
    """A drift probe that records how many times it was invoked."""

    def __init__(self, magnitude: float) -> None:
        self.magnitude = magnitude
        self.calls = 0

    def __call__(self, anchor: CharterAnchor) -> BiasProbeResult:
        self.calls += 1
        return BiasProbeResult(probe_type="position", detected=self.magnitude > 0.1, magnitude=self.magnitude, details="")


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


def test_already_evaluated_candidate_with_current_fingerprint_is_skipped(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    charter = _charter([_target("competitor-local", "anchor-v1")])
    current = eval_fingerprint(charter.anchor, "anchor-v1")
    stored = {"score": 0.5, "fingerprint": current}
    _candidate(registry, "cand-1", "competitor-local", metadata={"eval": stored})
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert reloaded.metadata["eval"] == stored  # untouched: fingerprint matches the current config
    assert _events(tmp_path) == []


def test_disabled_records_are_ignored(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    # only candidate and active records are scored; a disabled/deprecated record is left alone.
    _candidate(registry, "disabled-1", "competitor-local", activation_state="disabled")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("disabled-1")
    assert reloaded is not None and "eval" not in reloaded.metadata


def test_active_incumbent_rescored_when_anchor_changes(tmp_path: Path) -> None:
    # AC-883: when the charter anchor rotates, the active incumbent's eval was scored under the old
    # anchor and never matches new candidates, freezing promotion forever. the evaluate stage now
    # re-scores the incumbent under the current anchor so candidate vs incumbent is apples-to-apples.
    registry = ModelRegistry(tmp_path / "registry")
    stale = {"anchor_model": "claude-old", "score": 0.4, "fingerprint": "stale-old-anchor-fp"}
    _candidate(registry, "inc-1", "competitor-local", activation_state="active", metadata={"eval": stale})
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1"), ("p2", "r2")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = _stage(tmp_path, registry, _MapScorer({"p1": 0.7, "p2": 0.9}), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    reloaded = registry.load("inc-1")
    assert reloaded is not None
    assert reloaded.activation_state == "active"  # re-scoring never changes activation state
    eval_meta = reloaded.metadata["eval"]
    assert eval_meta["anchor_model"] == "claude-sonnet-5"
    assert eval_meta["score"] == 0.8
    assert eval_meta["fingerprint"] == eval_fingerprint(charter.anchor, "anchor-v1")


def test_active_incumbent_with_current_fingerprint_is_not_rescored(tmp_path: Path) -> None:
    # a still-current incumbent must not be re-scored every cycle (no churn); the fingerprint gate skips it.
    registry = ModelRegistry(tmp_path / "registry")
    charter = _charter([_target("competitor-local", "anchor-v1")])
    current = eval_fingerprint(charter.anchor, "anchor-v1")
    stored = {"anchor_model": "claude-sonnet-5", "score": 0.6, "fingerprint": current}
    _candidate(registry, "inc-1", "competitor-local", activation_state="active", metadata={"eval": stored})
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("inc-1")
    assert reloaded is not None and reloaded.metadata["eval"] == stored  # untouched
    assert _events(tmp_path) == []


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


def test_drift_probe_computed_once_per_run_once(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-a", "target-a")
    _candidate(registry, "cand-b", "target-b")
    _seed_suite(tmp_path / "suites", "suite-a", [("p1", "r1")])
    _seed_suite(tmp_path / "suites", "suite-b", [("p2", "r2")])
    charter = _charter([_target("target-a", "suite-a"), _target("target-b", "suite-b")])
    probe = _CountingProbe(0.3)
    stage = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: _FixedScorer(0.9),
        probe_fn=probe,
        drift_tolerance=0.5,
        now_fn=lambda: _NOW,
    )

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (2, 0)
    assert probe.calls == 1  # the canary depends only on the anchor, so it runs once per cycle
    a = registry.load("cand-a")
    b = registry.load("cand-b")
    assert a is not None and b is not None
    assert a.metadata["eval"]["drift_magnitude"] == 0.3
    assert b.metadata["eval"]["drift_magnitude"] == 0.3
    assert a.metadata["eval"]["drift_ok"] is True and b.metadata["eval"]["drift_ok"] is True


def test_all_no_suite_run_never_probes(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    charter = _charter([_target("competitor-local", "missing-suite")])
    probe = _CountingProbe(0.1)
    stage = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: _FixedScorer(0.9),
        probe_fn=probe,
        now_fn=lambda: _NOW,
    )

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    assert probe.calls == 0  # nothing to score, so the probe provider is never built


def test_all_skipped_run_never_probes(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    # a disabled record is never scored, so nothing is eligible and the probe provider stays unbuilt.
    _candidate(registry, "disabled-1", "competitor-local", activation_state="disabled")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    probe = _CountingProbe(0.1)
    stage = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: _FixedScorer(0.9),
        probe_fn=probe,
        now_fn=lambda: _NOW,
    )

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    assert probe.calls == 0


def test_drift_ok_boundary_is_inclusive(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    # magnitude exactly equal to the tolerance: drift_ok is True because the check is <= (inclusive).
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.2, drift_tolerance=0.2)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert reloaded.metadata["eval"]["drift_magnitude"] == 0.2
    assert reloaded.metadata["eval"]["drift_ok"] is True


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


def test_from_candidate_generation_defaults_false_placeholder(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    # _stage leaves scores_candidate_generation at its False default: the score judged the suite's
    # reference text, a placeholder, so the eval is stamped from_candidate_generation False.
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert reloaded.metadata["eval"]["from_candidate_generation"] is False


def test_from_candidate_generation_stamped_true_when_flag_set(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    stage = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: _FixedScorer(0.9),
        probe_fn=_probe(0.1),
        now_fn=lambda: _NOW,
        scores_candidate_generation=True,
    )

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert reloaded.metadata["eval"]["from_candidate_generation"] is True


def test_matching_fingerprint_candidate_is_not_rescored(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    charter = _charter([_target("competitor-local", "anchor-v1")])
    current = eval_fingerprint(charter.anchor, "anchor-v1")
    stored = {"score": 0.42, "anchor_model": "claude-sonnet-5", "fingerprint": current}
    _candidate(registry, "cand-1", "competitor-local", metadata={"eval": stored})
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (0, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    assert reloaded.metadata["eval"] == stored  # unchanged: still scored under the current config
    assert _events(tmp_path) == []


def test_stale_fingerprint_candidate_is_reevaluated(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    charter = _charter([_target("competitor-local", "anchor-v1")])
    # an eval scored under a previous anchor: its fingerprint carries a different anchor model, so it
    # no longer matches the current config and must be re-scored rather than left stuck.
    stale_anchor = CharterAnchor(provider="anthropic", model="claude-opus-4-8", rubric="Score 0 to 1.")
    stale = {"score": 0.42, "anchor_model": "claude-opus-4-8", "fingerprint": eval_fingerprint(stale_anchor, "anchor-v1")}
    _candidate(registry, "cand-1", "competitor-local", metadata={"eval": stale})
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])
    stage = _stage(tmp_path, registry, _FixedScorer(0.9), magnitude=0.1)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    reloaded = registry.load("cand-1")
    assert reloaded is not None
    eval_meta = reloaded.metadata["eval"]
    assert eval_meta["score"] == 0.9  # re-scored under the current anchor
    assert eval_meta["anchor_model"] == "claude-sonnet-5"
    assert eval_meta["fingerprint"] == eval_fingerprint(charter.anchor, "anchor-v1")


def test_drift_probe_memoized_across_reevaluations(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    charter = _charter([_target("target-a", "suite-a"), _target("target-b", "suite-b")])
    # both candidates carry a stale eval (previous anchor), so both are re-evaluated this cycle. the
    # drift canary depends only on the anchor, so it must still be probed exactly once for the cycle.
    stale_anchor = CharterAnchor(provider="anthropic", model="claude-opus-4-8", rubric="Score 0 to 1.")
    _candidate(
        registry,
        "cand-a",
        "target-a",
        metadata={"eval": {"score": 0.1, "fingerprint": eval_fingerprint(stale_anchor, "suite-a")}},
    )
    _candidate(
        registry,
        "cand-b",
        "target-b",
        metadata={"eval": {"score": 0.1, "fingerprint": eval_fingerprint(stale_anchor, "suite-b")}},
    )
    _seed_suite(tmp_path / "suites", "suite-a", [("p1", "r1")])
    _seed_suite(tmp_path / "suites", "suite-b", [("p2", "r2")])
    probe = _CountingProbe(0.3)
    stage = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: _FixedScorer(0.9),
        probe_fn=probe,
        drift_tolerance=0.5,
        now_fn=lambda: _NOW,
    )

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (2, 0)
    assert probe.calls == 1  # memoized across both re-evaluations
    a = registry.load("cand-a")
    b = registry.load("cand-b")
    assert a is not None and a.metadata["eval"]["score"] == 0.9
    assert b is not None and b.metadata["eval"]["score"] == 0.9


def test_anchor_rotation_deadlock_is_broken_by_incumbent_reeval(tmp_path: Path) -> None:
    # AC-883 end to end: after the charter anchor rotates, the evaluate stage re-scores the stale
    # incumbent under the new anchor, so the promote stage sees matching anchors, compares on merits
    # (no promote_anchor_mismatch), and a better candidate is finally promotable again.
    registry = ModelRegistry(tmp_path / "registry")
    # full autonomy so the promote stage auto-activates rather than parking the winner for approval.
    charter = Charter(
        tier="oss",
        autonomy="full",  # type: ignore[arg-type]
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=[_target("competitor-local", "anchor-v1")],
        budgets=CharterBudgets(gpu_hours_per_window=10.0, window_hours=24, disk_quota_gb=1.0),
        anchor=CharterAnchor(provider="anthropic", model="claude-sonnet-5", rubric="Score 0 to 1."),
    )
    # the stage below runs in real-generation mode (scores_candidate_generation=True), so the
    # candidate's already-current fingerprint must carry that same mode to be correctly skipped.
    current_fp = eval_fingerprint(charter.anchor, "anchor-v1", True)
    # incumbent scored under the OLD anchor (stale fingerprint) -> re-scored to 0.8 under the new one.
    _candidate(
        registry,
        "inc-1",
        "competitor-local",
        activation_state="active",
        metadata={
            "eval": {
                "anchor_model": "claude-old",
                "score": 0.6,
                "fingerprint": "old-anchor-fp",
                "from_candidate_generation": True,
                "drift_ok": True,
            }
        },
    )
    # candidate already scored under the CURRENT anchor -> evaluate skips it, keeping its 0.9.
    _candidate(
        registry,
        "cand-1",
        "competitor-local",
        metadata={
            "eval": {
                "anchor_model": "claude-sonnet-5",
                "score": 0.9,
                "fingerprint": current_fp,
                "from_candidate_generation": True,
                "drift_ok": True,
            }
        },
    )
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "r1")])

    evaluate = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: _FixedScorer(0.8),
        probe_fn=_probe(0.1),
        now_fn=lambda: _NOW,
        scores_candidate_generation=True,
    )
    evaluate.run_once(_ctx(tmp_path, charter))

    reanchored = registry.load("inc-1")
    assert reanchored is not None
    assert reanchored.metadata["eval"]["anchor_model"] == "claude-sonnet-5"  # re-scored under new anchor

    PromoteStage(name="promote", registry=registry, now_fn=lambda: _NOW).run_once(_ctx(tmp_path, charter))

    promoted = registry.load("cand-1")
    incumbent = registry.load("inc-1")
    assert promoted is not None and promoted.activation_state == "active"  # 0.9 beat the re-scored 0.8
    assert incumbent is not None and incumbent.activation_state == "disabled"
    assert not any(e["event"] == "promote_anchor_mismatch" for e in _events(tmp_path))


class _RecordingScorer:
    """A scorer that records the (prompt, output) pairs it judged, to prove WHAT was scored."""

    def __init__(self, value: float) -> None:
        self.value = value
        self.seen: list[tuple[str, str]] = []

    def score(self, prompt: str, output: str) -> float:
        self.seen.append((prompt, output))
        return self.value


def test_generate_fn_scores_real_candidate_generation_not_reference(tmp_path: Path) -> None:
    # AC-891: with a generate_fn wired, the scorer judges the candidate's real generation (not the
    # placeholder reference), and the eval is stamped from_candidate_generation True (promotable).
    registry = ModelRegistry(tmp_path / "registry")
    _candidate(registry, "cand-1", "competitor-local")
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "REFERENCE-TEXT")])
    charter = _charter([_target("competitor-local", "anchor-v1")])
    scorer = _RecordingScorer(0.7)
    gen_calls: list[tuple[str, str, str]] = []

    def fake_generate(record: DistilledModelRecord, anchor: CharterAnchor, prompt: str) -> str:
        gen_calls.append((record.artifact_id, anchor.model, prompt))
        return f"GENERATED::{prompt}"

    stage = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: scorer,
        probe_fn=_probe(0.1),
        now_fn=lambda: _NOW,
        generate_fn=fake_generate,
    )

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)
    assert gen_calls == [("cand-1", "claude-sonnet-5", "p1")]
    assert scorer.seen == [("p1", "GENERATED::p1")]  # the generation was scored, NOT "REFERENCE-TEXT"
    eval_meta = registry.load("cand-1").metadata["eval"]  # type: ignore[union-attr]
    assert eval_meta["score"] == 0.7
    assert eval_meta["from_candidate_generation"] is True
    assert eval_meta["fingerprint"] == eval_fingerprint(charter.anchor, "anchor-v1", True)


def test_wiring_real_generation_retriggers_a_placeholder_scored_candidate(tmp_path: Path) -> None:
    # AC-891 fingerprint fix: a candidate scored in placeholder mode is re-evaluated once real
    # generation is wired, even though the anchor and suite are unchanged.
    registry = ModelRegistry(tmp_path / "registry")
    charter = _charter([_target("competitor-local", "anchor-v1")])
    placeholder_fp = eval_fingerprint(charter.anchor, "anchor-v1")  # scg=False (placeholder mode)
    _candidate(
        registry,
        "cand-1",
        "competitor-local",
        metadata={"eval": {"score": 0.3, "fingerprint": placeholder_fp, "from_candidate_generation": False}},
    )
    _seed_suite(tmp_path / "suites", "anchor-v1", [("p1", "ref")])
    stage = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=tmp_path / "suites",
        judge_factory=lambda anchor: _FixedScorer(0.9),
        probe_fn=_probe(0.1),
        now_fn=lambda: _NOW,
        generate_fn=lambda record, anchor, prompt: f"gen::{prompt}",
    )

    result = stage.run_once(_ctx(tmp_path, charter))

    assert (result.processed, result.errors) == (1, 0)  # re-evaluated despite same anchor + suite
    eval_meta = registry.load("cand-1").metadata["eval"]  # type: ignore[union-attr]
    assert eval_meta["score"] == 0.9
    assert eval_meta["from_candidate_generation"] is True
    assert eval_meta["fingerprint"] == eval_fingerprint(charter.anchor, "anchor-v1", True)
