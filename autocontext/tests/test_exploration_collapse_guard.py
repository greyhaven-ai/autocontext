from __future__ import annotations

import json
from importlib import import_module
from types import SimpleNamespace
from typing import Any

_guard = import_module("autocontext.analytics.exploration_collapse_guard")
ExplorationSnapshot = _guard.ExplorationSnapshot
GuidanceChange = _guard.GuidanceChange
detect_exploration_collapse = _guard.detect_exploration_collapse
persist_exploration_collapse_report = _guard.persist_exploration_collapse_report
render_exploration_collapse_report = _guard.render_exploration_collapse_report


def _snapshot(
    generation_index: int,
    response_length: int,
    diversity: float,
    entropy: float,
    route_signature: str,
    rollback_rate: float,
    score: float,
) -> Any:
    return ExplorationSnapshot(
        generation_index=generation_index,
        response_length=response_length,
        diversity=diversity,
        entropy=entropy,
        route_signature=route_signature,
        rollback_rate=rollback_rate,
        score=score,
    )


def _collapsed_run() -> tuple[list[Any], list[Any]]:
    snapshots = [
        _snapshot(0, 120, 0.82, 3.3, "wide-a", 0.05, 0.61),
        _snapshot(1, 118, 0.78, 3.1, "wide-b", 0.04, 0.62),
        _snapshot(2, 42, 0.22, 0.9, "shortcut", 0.31, 0.55),
        _snapshot(3, 39, 0.2, 0.8, "shortcut", 0.34, 0.54),
    ]
    changes = [
        GuidanceChange(
            change_id="hint-set-v2",
            generation_index=2,
            kind="hint",
            source_component="soft_hints",
            source_span="hint:force-short-route",
        )
    ]
    return snapshots, changes


def test_advisory_guard_detects_length_diversity_collapse_after_hint() -> None:
    snapshots, changes = _collapsed_run()

    report = detect_exploration_collapse(snapshots, changes, advisory_only=True)

    assert len(report.events) == 1
    event = report.events[0]
    assert event.guidance_change.change_id == "hint-set-v2"
    assert event.guidance_change.source_component == "soft_hints"
    assert event.guidance_change.source_span == "hint:force-short-route"
    assert event.advisory_only is True
    assert event.mitigation == "none"
    assert {signal.metric for signal in event.signals} >= {
        "response_length",
        "diversity",
        "entropy",
        "route_repetition",
        "rollback_rate",
    }

    rendered = render_exploration_collapse_report(report)
    assert "hint-set-v2" in rendered
    assert "soft_hints" in rendered
    assert "hint:force-short-route" in rendered


def test_auto_mitigation_is_opt_in_and_report_is_persistable(tmp_path) -> None:
    snapshots, changes = _collapsed_run()

    advisory = detect_exploration_collapse(snapshots, changes, advisory_only=True)
    auto = detect_exploration_collapse(snapshots, changes, advisory_only=False, auto_mitigation=True)

    assert advisory.events[0].mitigation == "none"
    assert auto.events[0].mitigation == "demote_guidance"

    output = tmp_path / "exploration-collapse.json"
    persist_exploration_collapse_report(output, auto)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["events"][0]["event_type"] == "exploration_collapse_detected"
    assert payload["events"][0]["payload"]["guidance_change"]["change_id"] == "hint-set-v2"


def test_settings_guard_persists_generation_artifact_when_guidance_collapses(tmp_path) -> None:
    _persist_skill_note = import_module("autocontext.loop.stage_helpers.persistence_helpers")._persist_skill_note

    class Artifacts:
        def persist_skill_note(self, **_kwargs: Any) -> None:
            pass

        def append_dead_end(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def generation_dir(self, run_id: str, generation_index: int):
            return tmp_path / run_id / "generations" / f"gen_{generation_index}"

        def write_json(self, path, payload: dict[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")

    ctx = SimpleNamespace(
        tournament=SimpleNamespace(best_score=0.54),
        outputs=SimpleNamespace(coach_lessons="", coach_playbook=""),
        gate_decision="rollback",
        gate_delta=-0.08,
        generation=1,
        settings=SimpleNamespace(
            ablation_no_feedback=False,
            backpressure_min_delta=0.005,
            dead_end_tracking_enabled=False,
            exploration_collapse_guard=True,
            exploration_collapse_auto_mitigation=False,
        ),
        current_strategy={"route": "shortcut"},
        replay_narrative="short repeated answer",
        attempt=0,
        require_playbook_approval=False,
        scenario_name="grid_ctf",
        run_id="run-guard",
        score_history=[0.62, 0.54],
        gate_decision_history=["advance", "rollback"],
        applied_competitor_hints="Try the shortcut route.",
        base_playbook="",
    )

    _persist_skill_note(ctx, artifacts=Artifacts())

    payload = json.loads(
        (tmp_path / "run-guard" / "generations" / "gen_1" / "exploration_collapse_guard.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["events"][0]["event_type"] == "exploration_collapse_detected"
    assert payload["events"][0]["payload"]["guidance_change"]["source_component"] == "competitor_hints"


def test_no_warning_when_guidance_does_not_reduce_exploration() -> None:
    snapshots = [
        _snapshot(0, 100, 0.5, 2.0, "a", 0.1, 0.5),
        _snapshot(1, 102, 0.52, 2.1, "b", 0.08, 0.51),
        _snapshot(2, 99, 0.51, 2.0, "c", 0.09, 0.52),
        _snapshot(3, 101, 0.5, 2.0, "d", 0.1, 0.53),
    ]
    changes = [
        GuidanceChange(
            change_id="teacher-v1",
            generation_index=2,
            kind="teacher_signal",
            source_component="teacher",
        )
    ]

    report = detect_exploration_collapse(snapshots, changes)

    assert report.events == []
