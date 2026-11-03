"""Toy scorer contracts, not official benchmark or model-quality validation."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem
from autocontext.weight_evolution.scorers import ScorerInfrastructureError


def math_problem(answer: str = "42") -> EvaluationProblem:
    return EvaluationProblem(problem_id="fixture/eval/addition", text="What is 17 + 25?", answer=answer)


def instruction_problem(*instructions: tuple[str, dict[str, Any]]) -> EvaluationProblem:
    return EvaluationProblem(
        problem_id="fixture/eval/instructions", text="Write two uppercase words, including BLUE.",
        instructions=tuple(DatasetInstruction(instruction_id=name, kwargs=args) for name, args in instructions),
    )


def response(text: str, *, problem_id: str = "fixture/eval/addition", finish: str = "stop") -> Any:
    return SimpleNamespace(problem_id=problem_id, text=text, finish_reason=finish)


@pytest.mark.parametrize("dataset", ["math500", "gsm8k"])
@pytest.mark.parametrize(("text", "score"), [
    (r"Reasoning.</think>\boxed{42}", 1),
    (r"\boxed{+00042}", 1),
    (r"\boxed{41}", 0),
    (r"\boxed{42.0}", 0),
    (r"\boxed{6*7}", 0),
    ("42", 0),
    ("", 0),
    (r"\boxed{42", 0),
    (r"\boxed{42} and \boxed{7}", 0),
])
def test_fixture_integer_scoring_uses_real_extraction_but_no_symbolic_fallback(dataset: str, text: str, score: int) -> None:
    scorer = SyntheticComparisonScorer()
    assert scorer.score_sample(dataset, math_problem(), response(text)) == score


def test_length_limited_answer_is_scored_not_an_infrastructure_error() -> None:
    scorer = SyntheticComparisonScorer()
    assert scorer.score_sample("math500", math_problem(), response(r"\boxed{42}", finish="length")) == 1
    assert scorer.score_sample("math500", math_problem(), response("unfinished", finish="length")) == 0


@pytest.mark.parametrize("gold", ["1/2", "42.0", "unparseable", "1" * 129])
def test_invalid_gold_fails_preflight_even_if_response_is_empty(gold: str) -> None:
    scorer = SyntheticComparisonScorer()
    with pytest.raises(ScorerInfrastructureError, match="bounded signed decimal"):
        scorer.validate_cohort("gsm8k", (math_problem(gold),))
    with pytest.raises(ScorerInfrastructureError, match="bounded signed decimal"):
        scorer.score_sample("gsm8k", math_problem(gold), response(""))


def test_synthetic_instruction_checkers_use_strict_completed_thinking_boundary() -> None:
    problem = instruction_problem(("fixture:contains_word", {"word": "blue"}),
                                  ("fixture:word_count", {"count": 2}), ("fixture:uppercase", {}))
    scorer = SyntheticComparisonScorer()
    scorer.validate_cohort("ifeval", (problem,))
    assert scorer.score_sample("ifeval", problem, response("reasoning</think>BLUE SKY", problem_id=problem.problem_id)) == 1
    assert scorer.score_sample("ifeval", problem, response("BLUE SKY", problem_id=problem.problem_id)) == 0
    assert scorer.score_sample("ifeval", problem, response("</think>Blue sky", problem_id=problem.problem_id)) == 0
    assert scorer.score_sample("ifeval", problem, response("</think>BLUE", problem_id=problem.problem_id)) == 0
    assert scorer.score_sample("ifeval", problem, response("</think>BLUER SKY", problem_id=problem.problem_id)) == 0


@pytest.mark.parametrize(("instruction", "kwargs"), [
    ("keywords:existence", {"keywords": ["BLUE"]}),
    ("fixture:contains_word", {"word": ""}),
    ("fixture:contains_word", {"word": "two words"}),
    ("fixture:contains_word", {"word": " blue"}),
    ("fixture:contains_word", {"word": None}),
    ("fixture:contains_word", {"word": "blue", "unknown": True}),
    ("fixture:word_count", {"count": True}),
    ("fixture:word_count", {"count": -1}),
    ("fixture:word_count", {"count": 1001}),
    ("fixture:word_count", {"count": "2"}),
    ("fixture:uppercase", {"word": "BLUE"}),
])
def test_unsupported_or_malformed_instruction_is_not_a_model_zero(instruction: str, kwargs: dict[str, Any]) -> None:
    problem = instruction_problem((instruction, kwargs))
    scorer = SyntheticComparisonScorer()
    with pytest.raises(ScorerInfrastructureError, match="unknown or malformed"):
        scorer.validate_cohort("ifeval", (problem,))
    with pytest.raises(ScorerInfrastructureError, match="unknown or malformed"):
        scorer.score_sample("ifeval", problem, response("", problem_id=problem.problem_id))


def test_wrong_dataset_kind_and_response_identity_fail() -> None:
    scorer = SyntheticComparisonScorer()
    with pytest.raises(ScorerInfrastructureError, match="different problem"):
        scorer.score_sample("math500", math_problem(), response(r"\boxed{42}", problem_id="fixture/other"))
    with pytest.raises(ScorerInfrastructureError, match="instruction fixture"):
        scorer.validate_cohort("ifeval", (math_problem(),))
    with pytest.raises(ScorerInfrastructureError, match="bounded signed decimal"):
        scorer.validate_cohort("math500", (instruction_problem(("fixture:uppercase", {})),))
    with pytest.raises(ScorerInfrastructureError, match="unknown comparison"):
        scorer.validate_cohort("official_math", (math_problem(),))


def test_empty_and_duplicate_cohorts_fail() -> None:
    scorer = SyntheticComparisonScorer()
    with pytest.raises(ScorerInfrastructureError, match="nonempty"):
        scorer.validate_cohort("math500", ())
    with pytest.raises(ScorerInfrastructureError, match="unique"):
        scorer.validate_cohort("math500", (math_problem(), math_problem()))


@pytest.mark.parametrize("finish", ["error", "cancelled", "timeout"])
def test_worker_failure_finish_reason_cannot_be_scored_as_wrong_answer(finish: str) -> None:
    with pytest.raises(ScorerInfrastructureError, match="did not finish normally"):
        SyntheticComparisonScorer().score_sample("math500", math_problem(), response("", finish=finish))


def test_provenance_is_permanently_toy_and_independent_of_caller_mutation() -> None:
    scorer = SyntheticComparisonScorer()
    original = scorer.lock_digest
    provenance = scorer.provenance()
    assert provenance["execution_kind"] == scorer.execution_kind == "fixture"
    assert provenance["official_benchmark_grader"] is False
    assert provenance["benchmark_membership"] is False
    provenance["instruction_checkers"]["fixture:uppercase"] = "official checker"
    provenance["execution_kind"] = "official"
    assert scorer.lock_digest == original
    assert scorer.provenance()["execution_kind"] == "fixture"


@pytest.mark.parametrize("filename", ["comparison_fixtures.py", "scorers.py"])
def test_implementation_change_invalidates_scorer_identity(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    scorer = SyntheticComparisonScorer()
    original = scorer.lock_digest
    original_read = Path.read_bytes

    def changed(path: Path) -> bytes:
        data = original_read(path)
        return data + b"\n# changed fixture implementation\n" if path.name == filename else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert scorer.lock_digest != original


def test_fixture_scorer_never_loads_optional_model_or_official_checker_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("fixture scoring attempted a dynamic dependency import")

    monkeypatch.setattr(importlib, "import_module", unavailable)
    scorer = SyntheticComparisonScorer()
    assert scorer.score_sample("gsm8k", math_problem(), response(r"\boxed{42}")) == 1
    problem = instruction_problem(("fixture:uppercase", {}))
    assert scorer.score_sample("ifeval", problem, response("</think>BLUE", problem_id=problem.problem_id)) == 1
