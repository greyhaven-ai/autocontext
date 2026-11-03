from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_weight_comparison_models import comparison_context

from autocontext.weight_evolution.comparison_evaluation import (
    FixtureEvidence,
    FixtureObservation,
    evaluate_comparison_fixture,
    fixture_observation,
    load_comparison_evidence,
    render_comparison_report,
    write_comparison_report,
)
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_models import ComparisonCase, build_comparison_plan
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem
from autocontext.weight_evolution.scorers import ScorerInfrastructureError


def evaluation_context() -> SimpleNamespace:
    context = comparison_context()
    scorer = SyntheticComparisonScorer()
    cases = tuple(
        ComparisonCase(dataset=dataset, problem=EvaluationProblem(problem_id=name, text=prompt, answer=answer))
        for dataset, name, prompt, answer in (
            ("math500", "math/a", "What is three plus four?", "7"),
            ("math500", "math/b", "What is eight minus three?", "5"),
            ("gsm8k", "gsm/a", "How many wheels on three tricycles?", "9"),
            ("gsm8k", "gsm/b", "How many eggs in a dozen?", "12"),
        )
    ) + tuple(
        ComparisonCase(dataset="ifeval", problem=EvaluationProblem(
            problem_id=name, text=prompt,
            instructions=(DatasetInstruction(instruction_id="fixture:contains_word", kwargs={"word": word}),),
        )) for name, prompt, word in (("instruction/a", "Say hello.", "hello"), ("instruction/b", "Say goodbye.", "goodbye"))
    )
    protocol = context.protocol.model_copy(update={"scorer_digest": scorer.lock_digest})
    budgets = context.budgets.model_copy(update={"max_generated_units_per_condition": 2048,
                                               "max_total_generated_units": 10240})
    options = {**context.options, "protocol": protocol, "budgets": budgets, "cases": cases}
    plan = build_comparison_plan(**options)
    # Deliberately prerecorded separately from EvaluationProblem answers.
    responses = {"math/a": r"\boxed{7}", "math/b": r"\boxed{5}", "gsm/a": r"\boxed{9}",
                 "gsm/b": r"\boxed{12}", "instruction/a": "</think>hello", "instruction/b": "</think>goodbye"}
    observations = tuple(fixture_observation(
        cell,
        responses[cell.problem_id] if cell.condition_id in {"C", "CW"}
        or (cell.condition_id == "A" and cell.sample_index == 0)
        or (cell.condition_id == "W" and cell.problem_id.endswith("/b")) else "",
        elapsed_seconds=0.01,
    ) for cell in plan.cells)
    evidence = FixtureEvidence(plan_digest=plan.digest, observations=observations)
    return SimpleNamespace(plan=plan, scorer=scorer, evidence=evidence, options=options)


def changed_evidence(context: SimpleNamespace, **change: Any) -> FixtureEvidence:
    payload = context.evidence.to_dict()
    payload["observations"][0].update(change)
    return FixtureEvidence.model_validate_json(json.dumps(payload))


def test_grouped_accuracy_pairs_problem_blocks_across_all_seeds_and_samples() -> None:
    context = evaluation_context()
    result = evaluate_comparison_fixture(context.plan, context.evidence, context.scorer).to_dict()
    metrics = {(m["condition_id"], m["dataset"]): m for m in result["metrics"]}
    assert metrics[("A", "math500")]["mean_sample_accuracy"] == 0.25
    assert metrics[("A", "math500")]["samples_per_problem_across_seeds"] == 8
    assert metrics[("A", "math500")]["n_samples"] == 16
    assert metrics[("CW", "math500")]["mean_sample_accuracy"] == 1.0
    assert metrics[("W", "math500")]["mean_sample_accuracy"] == 0.5
    assert len(result["contrasts"]) == 15
    assert all(c["n_problem_blocks"] == 2 for c in result["contrasts"])
    assert all(c["simultaneous_coverage_claimed"] is False for c in result["contrasts"])
    contrast = next(c for c in result["contrasts"] if c["contrast"] == "CW-W" and c["dataset"] == "math500")
    assert contrast["mean"] == 0.5
    interaction = next(c for c in result["factorial_interactions"] if c["dataset"] == "math500")
    assert interaction["mean"] == -0.25


def test_fixture_labels_cost_attribution_and_artifact_provenance_remain_explicit() -> None:
    context = evaluation_context()
    evaluation = evaluate_comparison_fixture(context.plan, context.evidence, context.scorer)
    result = evaluation.to_dict()
    assert result["evidence_kind"] == "offline_comparison_fixture"
    assert result["execution_kind"] == "fixture" and result["model_execution_performed"] is False
    assert result["gpu_execution_deferred"] is True
    for key in ("benchmark_result_claimed", "promotion_claimed", "retained_learning_claimed"):
        assert result[key] is False
    arms = {a["condition_id"]: a for a in result["conditions"]}
    assert arms["CW"]["declared_search_calls"] == 5
    assert result["resource_plan"]["declared_unique_search_calls"] == 6
    assert len(result["artifact_provenance"]) == 6
    assert result["artifact_provenance"]["base_checkpoint"]["artifact_digest"] == context.plan.artifacts.base_checkpoint.digest
    assert "Synthetic fixtures only" in render_comparison_report(evaluation)
    assert "not model output" in render_comparison_report(evaluation)
    assert "gold-only" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"condition_id": "W"}, {"dataset": "gsm8k"}, {"problem_id": "other"},
    {"sample_index": 11}, {"seed": 123}, {"input_units": 1}, {"cell_digest": "f" * 64},
])
def test_changed_cell_labels_or_prompt_identity_are_rejected_before_scoring(change: dict[str, Any]) -> None:
    context = evaluation_context()
    evidence = changed_evidence(context, **change)
    with pytest.raises(ValueError, match="fixture"):
        evaluate_comparison_fixture(context.plan, evidence, context.scorer)


def test_missing_surplus_duplicate_and_wrong_plan_evidence_are_rejected() -> None:
    context = evaluation_context()
    with pytest.raises(ValueError, match="every planned"):
        evaluate_comparison_fixture(context.plan, context.evidence.model_copy(
            update={"observations": context.evidence.observations[:-1]}), context.scorer)
    with pytest.raises(ValueError, match="different comparison plan"):
        evaluate_comparison_fixture(context.plan, context.evidence.model_copy(update={"plan_digest": "d" * 64}), context.scorer)
    data = context.evidence.to_dict()
    data["observations"].append(data["observations"][0])
    with pytest.raises(ValueError, match="duplicate fixture cell"):
        FixtureEvidence.model_validate_json(json.dumps(data))
    data["observations"][-1]["cell_digest"] = "d" * 64
    # Copies alias in the Python payload above; reset and append a distinct extra row.
    data = context.evidence.to_dict()
    data["observations"].append({**data["observations"][0], "cell_digest": "d" * 64})
    with pytest.raises(ValueError, match="every planned"):
        evaluate_comparison_fixture(context.plan, FixtureEvidence.model_validate_json(json.dumps(data)), context.scorer)


@pytest.mark.parametrize("change", [
    {"execution_kind": "vllm"}, {"response_sha256": "f" * 64}, {"output_units": 200},
    {"finish_reason": "error"}, {"elapsed_seconds": -1.0}, {"elapsed_seconds": float("inf")},
])
def test_malformed_raw_fixture_observations_cannot_enter_evaluation(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        changed_evidence(evaluation_context(), **change)


@pytest.mark.parametrize("field,value", [("execution_kind", "vllm"), ("model_execution_performed", True)])
def test_evidence_cannot_be_relabelled_as_model_execution(field: str, value: Any) -> None:
    payload = evaluation_context().evidence.to_dict()
    payload[field] = value
    with pytest.raises(ValueError):
        FixtureEvidence.model_validate_json(json.dumps(payload))


def test_wrong_missing_and_length_limited_answers_stay_scored_observations() -> None:
    context = evaluation_context()
    cell = context.plan.cells[0]
    closed = r"\boxed{7}".ljust(cell.max_new_tokens, " ")
    observation = fixture_observation(cell, closed, finish_reason="length")
    evidence = context.evidence.model_copy(update={"observations": (observation, *context.evidence.observations[1:])})
    result = evaluate_comparison_fixture(context.plan, evidence, context.scorer).to_dict()
    assert result["samples"][0]["score"] == 1
    assert result["samples"][0]["finish_reason"] == "length"
    assert any(s["score"] == 0 and s["output_units"] == 0 for s in result["samples"])
    assert len(result["samples"]) == len(context.plan.cells)


def test_output_cap_length_termination_and_total_wall_budget_fail_closed() -> None:
    context = evaluation_context()
    for text, reason in (("x" * 65, "stop"), ("x" * 63, "length")):
        item = fixture_observation(context.plan.cells[0], text, finish_reason=reason)  # type: ignore[arg-type]
        evidence = context.evidence.model_copy(update={"observations": (item, *context.evidence.observations[1:])})
        with pytest.raises(ValueError, match="declared cap"):
            evaluate_comparison_fixture(context.plan, evidence, context.scorer)
    item = context.evidence.observations[0].model_copy(update={"elapsed_seconds": 61.0})
    evidence = context.evidence.model_copy(update={"observations": (item, *context.evidence.observations[1:])})
    with pytest.raises(ValueError, match="frozen comparison budget"):
        evaluate_comparison_fixture(context.plan, evidence, context.scorer)


def test_all_cohorts_preflight_before_first_score_and_errors_never_become_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    context = evaluation_context()
    events: list[str] = []
    monkeypatch.setattr(context.scorer, "validate_cohort", lambda name, problems: events.append(f"preflight:{name}"))
    monkeypatch.setattr(context.scorer, "score_sample", lambda dataset, problem, sample: events.append("score") or 0)
    evaluate_comparison_fixture(context.plan, context.evidence, context.scorer)
    assert events[:3] == ["preflight:gsm8k", "preflight:ifeval", "preflight:math500"]
    assert events[3] == "score"

    def fail(*args: Any) -> None:
        raise RuntimeError("broken checker")

    monkeypatch.setattr(context.scorer, "score_sample", fail)
    with pytest.raises(ScorerInfrastructureError, match="fixture scorer failed"):
        evaluate_comparison_fixture(context.plan, context.evidence, context.scorer)
    monkeypatch.setattr(context.scorer, "validate_cohort", fail)
    with pytest.raises(ScorerInfrastructureError, match="preflight failed"):
        evaluate_comparison_fixture(context.plan, context.evidence, context.scorer)


@pytest.mark.parametrize("invalid_score", [True, 0.5, -1, 2, None])
def test_invalid_checker_return_is_infrastructure_failure(invalid_score: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    context = evaluation_context()
    monkeypatch.setattr(context.scorer, "score_sample", lambda *args: invalid_score)
    with pytest.raises(ScorerInfrastructureError, match="integer zero or one"):
        evaluate_comparison_fixture(context.plan, context.evidence, context.scorer)


def test_changed_scorer_and_attempted_official_relabelling_are_rejected() -> None:
    context = evaluation_context()
    scorer = SimpleNamespace(lock_digest="d" * 64, execution_kind="fixture")
    with pytest.raises(ValueError, match="declared fixture scorer"):
        evaluate_comparison_fixture(context.plan, context.evidence, scorer)
    scorer.lock_digest, scorer.execution_kind = context.scorer.lock_digest, "official"
    with pytest.raises(ValueError, match="declared fixture scorer"):
        evaluate_comparison_fixture(context.plan, context.evidence, scorer)


def test_unicode_uses_exact_byte_units_and_maximum_seed_is_supported() -> None:
    cell = evaluation_context().plan.cells[0].model_copy(update={"seed": 4294967295})
    item = fixture_observation(cell, "é🙂")
    assert item.output_units == 6 and item.seed == 4294967295
    with pytest.raises(ValueError, match="byte count"):
        FixtureObservation.model_validate_json(json.dumps({**item.to_dict(), "output_units": 2}))


def test_report_files_are_deterministic_complete_last_and_reject_existing_or_symlink_outputs(tmp_path: Path) -> None:
    context = evaluation_context()
    evaluation = evaluate_comparison_fixture(context.plan, context.evidence, context.scorer)
    first, second = tmp_path / "first", tmp_path / "second"
    write_comparison_report(evaluation, first)
    write_comparison_report(evaluate_comparison_fixture(context.plan, context.evidence, context.scorer), second)
    assert {p.name for p in first.iterdir()} == {"report.json", "report.md", "complete.json"}
    for name in ("report.json", "report.md", "complete.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    marker = json.loads((first / "complete.json").read_text())
    assert marker["report_digest"] == evaluation.digest
    assert marker["json_sha256"] == hashlib.sha256((first / "report.json").read_bytes()).hexdigest()
    assert marker["markdown_sha256"] == hashlib.sha256((first / "report.md").read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="new directory"):
        write_comparison_report(evaluation, first)
    linked = tmp_path / "alias"
    linked.symlink_to(second, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        write_comparison_report(evaluation, linked / "new")


def test_bounded_fixture_loader_rejects_duplicate_keys_and_symlinks(tmp_path: Path) -> None:
    context = evaluation_context()
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(context.evidence.to_dict()))
    assert load_comparison_evidence(path) == context.evidence
    path.write_text('{"execution_kind":"fixture","execution_kind":"vllm"}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_comparison_evidence(path)
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        load_comparison_evidence(alias)


def test_library_model_copy_cannot_bypass_evidence_or_plan_validators() -> None:
    context = evaluation_context()
    duplicate = context.evidence.model_copy(update={"observations": (*context.evidence.observations,
                                                                     context.evidence.observations[0])})
    with pytest.raises(ValueError, match="duplicate fixture cell"):
        evaluate_comparison_fixture(context.plan, duplicate, context.scorer)
    dishonest = context.evidence.model_copy(update={"execution_kind": "vllm"})
    with pytest.raises(ValueError):
        evaluate_comparison_fixture(context.plan, dishonest, context.scorer)
    missing = context.plan.model_copy(update={"cells": context.plan.cells[:-1]})
    with pytest.raises(ValueError, match="schedule"):
        evaluate_comparison_fixture(missing, context.evidence, context.scorer)


@pytest.mark.parametrize("field,value", [("model_execution_performed", True), ("benchmark_result_claimed", True),
                                         ("promotion_claimed", True), ("execution_kind", "vllm")])
def test_report_cannot_relabel_synthetic_outputs_before_publication(tmp_path: Path, field: str, value: Any) -> None:
    context = evaluation_context()
    evaluation = evaluate_comparison_fixture(context.plan, context.evidence, context.scorer)
    evaluation.payload[field] = value
    with pytest.raises(ValueError, match="fixture claims"):
        write_comparison_report(evaluation, tmp_path / "report")
    assert not (tmp_path / "report").exists()
