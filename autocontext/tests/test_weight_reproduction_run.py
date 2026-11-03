"""Offline coordinator tests; mocked acceptance/scoring never measure a model."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from test_weight_cohort import EXAMPLE, replay_context

from autocontext.weight_evolution import reproduction
from autocontext.weight_evolution.cohort import build_replay_plan
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem, VerifiedDataset, cohort_digest
from autocontext.weight_evolution.evaluation import ReproductionEvaluation
from autocontext.weight_evolution.replay import ReplayIncompleteError

GPU_UUID = "GPU-01234567-89ab-cdef-0123-456789abcdef"


@pytest.fixture
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    context = replay_context(count=1)
    gsm_problem = EvaluationProblem(problem_id="synthetic/gsm", text="Synthetic GSM prompt", answer="1")
    if_problem = EvaluationProblem(problem_id="synthetic/if", text="Synthetic instruction prompt",
                                    instructions=(DatasetInstruction(instruction_id="keywords:existence",
                                                                     kwargs={"keywords": ["hello"]}),))
    datasets = (context.dataset, *(
        VerifiedDataset(name, cohort_digest((problem,)), (problem,), "a" * 64, context.dataset.lock_digest)
        for name, problem in (("gsm8k", gsm_problem), ("ifeval", if_problem))
    ))
    plan = build_replay_plan(context.lock, context.runtime, datasets, context.receipts,
                             scorer_lock_digest="c" * 64, published_results_digest="d" * 64, limits=context.limits)
    events: list[str] = []
    scorer = Mock(execution_kind="official", lock_digest=plan.scorer_lock_digest)
    scorer.provenance.return_value = {"implementation": "mock scorer for offline coordinator tests"}
    scorer.validate_cohort.side_effect = lambda dataset, problems: events.append(f"preflight:{dataset}")
    accepted = {"harness_digest": "9" * 64, "runtime_environment": {"python_version": "3.12.1"},
                "hardware": {"accelerator": "cuda", "device_uuid": GPU_UUID},
                "host_identity": {"gpu_uuid": GPU_UUID, "driver_version": "580.95.05", "daemon_id": "test-daemon"}}
    acceptance = Mock(side_effect=lambda *args, **kwargs: events.append("acceptance") or copy.deepcopy(accepted))
    monkeypatch.setattr(reproduction, "verify_gpu_acceptance", acceptance)
    readiness = Mock(return_value={"ready_for_acceptance": True,
                                   "observations": {"selected_gpu": {"uuid": GPU_UUID, "driver_version": "580.95.05"},
                                                    "daemon": {"id": "test-daemon"}}})
    monkeypatch.setattr(reproduction, "probe_gpu_readiness", readiness)
    dispatches: list[str] = []

    def mock_replay(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("replay")
        assert set(events[:3]) == {"preflight:math500", "preflight:gsm8k", "preflight:ifeval"}
        assert "acceptance" in events
        assert kwargs["expected_execution"] == {
            name: accepted[name] for name in ("harness_digest", "runtime_environment", "hardware")
        }
        assert kwargs["gpu_device"] == GPU_UUID
        runs_root = kwargs["output_dir"] / "runs"
        dispatched = 0
        for shard in plan.shards:
            directory = runs_root / shard.shard_id
            if not directory.exists():
                directory.mkdir(parents=True)
                dispatches.append(shard.shard_id)
                dispatched += 1
        return {"completed_shards": [shard.shard_id for shard in plan.shards], "dispatched_this_invocation": dispatched}

    replay = Mock(side_effect=mock_replay)
    monkeypatch.setattr(reproduction, "run_replay", replay)
    # Publication is real, but this intentionally minimal payload comes from a
    # mocked evaluator. Actual scorer/evidence behavior is tested separately.
    evaluation = ReproductionEvaluation({
        "evidence_kind": "full_cohort_replay", "plan_digest": plan.digest, "comparisons": [],
        "published_reference": {"sources": []}, "runtime_image": plan.runtime_image,
        "dataset_lock_digest": plan.dataset_lock_digest, "scorer_lock_digest": plan.scorer_lock_digest,
        "harness_digest": accepted["harness_digest"], "fidelity_notes": ["Synthetic coordinator test only."],
    })
    evaluator = Mock(return_value=evaluation)
    monkeypatch.setattr(reproduction, "evaluate_reproduction", evaluator)
    options = dict(acceptance_dir=tmp_path / "acceptance", datasets=datasets, scorer=scorer,
                   published=SimpleNamespace(digest=plan.published_results_digest),
                   lock_path=EXAMPLE / "reproduction.lock.json", runtime_path=EXAMPLE / "runtime.lock.json",
                   asset_root=tmp_path / "assets", output_dir=tmp_path / "study", gpu_device="0")
    return SimpleNamespace(plan=plan, options=options, accepted=accepted, acceptance=acceptance, readiness=readiness,
                           replay=replay, evaluator=evaluator, scorer=scorer, events=events, dispatches=dispatches)


def run(study: SimpleNamespace) -> dict[str, Any]:
    return reproduction.run_reproduction(study.plan, **study.options)


@pytest.mark.parametrize("mode", ["reproduction", "smoke"])
def test_full_study_rejects_single_dataset_or_smoke_before_any_dispatch(study: SimpleNamespace, mode: str) -> None:
    study.plan = replay_context(count=1, mode=mode).plan
    with pytest.raises(ValueError, match="all three complete"):
        run(study)
    study.replay.assert_not_called()
    study.scorer.validate_cohort.assert_not_called()


def test_resource_summary_reserves_reports_staging_and_metadata(study: SimpleNamespace) -> None:
    summary = reproduction.reproduction_resource_summary(study.plan)
    assert summary["acceptance_required"] is True
    assert summary["official_scorer_preflight_required"] is True
    assert summary["storage_reservation_bytes"] > (len(study.plan.shards) + 1) * study.plan.limits.max_report_bytes
    assert summary["model_execution_performed"] is False
    study.replay.assert_not_called()


@pytest.mark.parametrize("boundary", ["fixture", "scorer_lock", "published", "dataset"])
def test_unfrozen_evaluator_inputs_rejected_before_acceptance_or_dispatch(study: SimpleNamespace, boundary: str) -> None:
    if boundary == "fixture":
        study.scorer.execution_kind = "fixture"
    elif boundary == "scorer_lock":
        study.scorer.lock_digest = "e" * 64
    elif boundary == "published":
        study.options["published"] = SimpleNamespace(digest="e" * 64)
    else:
        study.options["datasets"] = study.options["datasets"][:-1]
    with pytest.raises(ValueError):
        run(study)
    study.acceptance.assert_not_called()
    study.replay.assert_not_called()


def test_every_official_scorer_cohort_preflight_precedes_dispatch(study: SimpleNamespace) -> None:
    run(study)
    assert set(study.events[:3]) == {"preflight:math500", "preflight:gsm8k", "preflight:ifeval"}
    assert study.events[3:] == ["acceptance", "replay"]
    for call in study.scorer.validate_cohort.call_args_list:
        assert call.args[1] == next(d.problems for d in study.options["datasets"] if d.dataset == call.args[0])


def test_scorer_preflight_failure_never_dispatches(study: SimpleNamespace) -> None:
    study.scorer.validate_cohort.side_effect = ValueError("invalid gold or instruction")
    with pytest.raises(ValueError, match="invalid gold"):
        run(study)
    study.acceptance.assert_not_called()
    study.replay.assert_not_called()
    assert not study.options["output_dir"].exists()


@pytest.mark.parametrize("error", [FileNotFoundError("missing acceptance"), ValueError("tampered acceptance")])
def test_missing_or_tampered_acceptance_never_dispatches(study: SimpleNamespace, error: Exception) -> None:
    study.acceptance.side_effect = error
    with pytest.raises(type(error), match="acceptance"):
        run(study)
    study.readiness.assert_not_called()
    study.replay.assert_not_called()
    assert not study.options["output_dir"].exists()


def test_blocked_readiness_returns_unexecuted_result_without_claims(study: SimpleNamespace) -> None:
    study.readiness.return_value = {"ready_for_acceptance": False, "blockers": ["No compatible GPU"]}
    result = run(study)
    assert result == {"ok": False, "stage": "gpu_readiness", "executed": False,
                      "readiness": study.readiness.return_value}
    study.replay.assert_not_called()
    study.evaluator.assert_not_called()
    assert not study.options["output_dir"].exists()


def test_selected_gpu_must_match_the_actual_accepted_uuid(study: SimpleNamespace) -> None:
    study.readiness.return_value["observations"]["selected_gpu"]["uuid"] = "GPU-ffffffff-ffff-ffff-ffff-ffffffffffff"
    with pytest.raises(ValueError, match="GPU UUID differs"):
        run(study)
    study.replay.assert_not_called()
    assert not study.options["output_dir"].exists()


@pytest.mark.parametrize("change", ["driver", "daemon", "missing_host_identity"])
def test_host_must_match_acceptance_before_dispatch(study: SimpleNamespace, change: str) -> None:
    if change == "driver":
        study.readiness.return_value["observations"]["selected_gpu"]["driver_version"] = "590.10.1"
    elif change == "daemon":
        study.readiness.return_value["observations"]["daemon"]["id"] = "replacement-daemon"
    else:
        study.accepted.pop("host_identity")
    with pytest.raises(ValueError, match="driver or Docker daemon differs"):
        run(study)
    study.replay.assert_not_called()
    study.evaluator.assert_not_called()
    assert not study.options["output_dir"].exists()


def test_success_publishes_complete_report_and_resume_regrades_without_redispatch(study: SimpleNamespace) -> None:
    first = run(study)
    count = len(study.plan.shards)
    assert first["dispatched_this_invocation"] == count
    assert first["behavioral_reproduction_claimed"] is False
    output = study.options["output_dir"]
    assert {path.name for path in (output / "report").iterdir()} == {"complete.json", "report.json", "report.md"}
    before = {p.name: p.read_bytes() for p in (output / "report").iterdir()}
    second = run(study)
    assert second["dispatched_this_invocation"] == 0
    assert second["report_digest"] == first["report_digest"]
    assert study.dispatches == [s.shard_id for s in study.plan.shards]
    assert before == {p.name: p.read_bytes() for p in (output / "report").iterdir()}
    assert study.acceptance.call_count == study.readiness.call_count == study.evaluator.call_count == 2
    assert study.scorer.validate_cohort.call_count == 6
    assert json.loads((output / "reproduction.receipt.json").read_bytes())["report_digest"] == first["report_digest"]


@pytest.mark.parametrize("kind", ["fixture", "smoke", "subset_replay"])
def test_nonfull_evaluation_cannot_publish_reproduction_success(study: SimpleNamespace, kind: str) -> None:
    study.evaluator.return_value.payload["evidence_kind"] = kind
    with pytest.raises(ValueError, match="cannot publish"):
        run(study)
    assert not (study.options["output_dir"] / "reproduction.receipt.json").exists()
    assert not (study.options["output_dir"] / "report").exists()


@pytest.mark.parametrize("target", ["report", "manifest", "acceptance", "receipt"])
def test_changed_resume_evidence_cannot_be_replaced_or_certified(study: SimpleNamespace, target: str) -> None:
    run(study)
    output = study.options["output_dir"]
    if target == "report":
        (output / "report" / "report.md").write_text("changed report")
    elif target == "manifest":
        (output / "manifest.json").write_text("{}")
    elif target == "acceptance":
        study.accepted["runtime_environment"]["python_version"] = "3.12.2"
    else:
        (output / "reproduction.receipt.json").write_text("{}")
    dispatches = list(study.dispatches)
    with pytest.raises(ReplayIncompleteError):
        run(study)
    assert study.dispatches == dispatches


def test_replay_failure_does_not_grade_or_publish_success(study: SimpleNamespace) -> None:
    study.replay.side_effect = ReplayIncompleteError("ambiguous dispatch")
    with pytest.raises(ReplayIncompleteError, match="ambiguous dispatch"):
        run(study)
    study.evaluator.assert_not_called()
    assert not (study.options["output_dir"] / "reproduction.receipt.json").exists()


@pytest.mark.parametrize("kind", ["fifo", "oversized"])
def test_saved_markdown_is_bounded_regular_file_before_read(study: SimpleNamespace, kind: str) -> None:
    run(study)
    report = study.options["output_dir"] / "report" / "report.md"
    original = report.read_bytes()
    if kind == "fifo":
        report.unlink()
        os.mkfifo(report)
    else:
        report.write_bytes(original + b"x")
    calls = list(study.dispatches)
    with pytest.raises(ValueError, match="regular file" if kind == "fifo" else "exceeds byte budget"):
        run(study)
    assert study.dispatches == calls
    if kind == "fifo":
        assert report.is_fifo()
    else:
        assert report.read_bytes() == original + b"x"
