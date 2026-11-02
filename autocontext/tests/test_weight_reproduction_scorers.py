from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.weight_evolution import scorers
from autocontext.weight_evolution.scorers import (
    IFEvalInstruction,
    ScoredSample,
    ScorerInfrastructureError,
    ScorerUnavailableError,
    extract_math_answer,
    grouped_sample_accuracy,
    math_verify_equivalent,
    score_ifeval,
    score_math,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures/weight_reproduction/scorers.json").read_text())


def _fixture_equivalence(prediction: str, gold: str) -> bool:
    """A finite fixture oracle, deliberately not a substitute math evaluator."""
    return (prediction, gold) in {(r"\frac{1}{2}", "0.5"), ("4", "4")}


@pytest.mark.parametrize("case", FIXTURES["math"], ids=lambda case: case["id"])
def test_math_protocol_fixtures(case: dict[str, Any]) -> None:
    result = score_math(
        case["text"], case["gold"], verifier=_fixture_equivalence, finish_reason=case.get("finish_reason", "stop")
    )
    assert extract_math_answer(case["text"]) == case["prediction"]
    assert result.prediction == case["prediction"]
    assert result.score == case["score"]
    assert result.finish_reason == case.get("finish_reason", "stop")


class _FixtureInstruction:
    """Two synthetic rules for exercising the official checker interface."""

    def __init__(self, instruction_id: str) -> None:
        self.instruction_id = instruction_id
        self.keyword = ""

    def build_description(self, **kwargs: object) -> str:
        assert "unused" not in kwargs  # None-valued official kwargs are removed.
        self.keyword = str(kwargs.get("keyword", self.keyword))
        return "synthetic checker fixture"

    def get_instruction_args(self) -> list[str]:
        return []

    def check_following(self, value: str) -> bool:
        if self.instruction_id == "fixture:keyword":
            return self.keyword.lower() in value.lower()
        return value.isupper()


INSTRUCTIONS = (
    IFEvalInstruction("fixture:keyword", {"keyword": "blue", "unused": None}),
    IFEvalInstruction("fixture:uppercase", {}),
)
REGISTRY = {name: _FixtureInstruction for name in ("fixture:keyword", "fixture:uppercase")}


@pytest.mark.parametrize("case", FIXTURES["ifeval"], ids=lambda case: case["id"])
def test_strict_prompt_ifeval_fixtures(case: dict[str, Any]) -> None:
    result = score_ifeval(
        case["text"], INSTRUCTIONS, prompt="Synthetic instructions", registry=REGISTRY,
        thinking=case["thinking"], finish_reason=case.get("finish_reason", "stop"),
    )
    assert result.response == case["response"]
    assert list(result.instruction_results) == case["instruction_results"]
    assert result.score == case["score"]


def test_ifeval_prompt_dependent_instruction_builds_original_prompt() -> None:
    descriptions: list[dict[str, object]] = []

    class PromptChecker(_FixtureInstruction):
        def build_description(self, **kwargs: object) -> str:
            descriptions.append(kwargs)
            return "uses the task prompt"

        def get_instruction_args(self) -> list[str]:
            return ["prompt"]

    result = score_ifeval(
        "</think>BLUE", [IFEvalInstruction("prompt", {})], prompt="ORIGINAL",
        registry={"prompt": PromptChecker},
    )
    assert result.score == 1
    assert descriptions == [{}, {"prompt": "ORIGINAL"}]


def test_instruction_failures_are_not_averaged() -> None:
    result = score_ifeval("</think>blue", INSTRUCTIONS, prompt="synthetic", registry=REGISTRY)
    assert result.instruction_results == (True, False)
    assert result.score == 0


def test_no_empty_instruction_success() -> None:
    with pytest.raises(ScorerInfrastructureError, match="at least one"):
        score_ifeval("anything", [], prompt="synthetic", registry={})


def test_ifeval_requires_explicit_registry() -> None:
    with pytest.raises(ScorerUnavailableError, match="registry"):
        score_ifeval("</think>BLUE", INSTRUCTIONS, prompt="synthetic")


def test_unknown_instruction_is_infrastructure_failure_even_for_empty_response() -> None:
    with pytest.raises(ScorerInfrastructureError, match="KeyError"):
        score_ifeval("", INSTRUCTIONS, prompt="synthetic", registry={})


def test_ifeval_checker_error_is_not_model_failure() -> None:
    class BrokenChecker(_FixtureInstruction):
        def check_following(self, value: str) -> bool:
            raise RuntimeError("missing official resource")

    with pytest.raises(ScorerInfrastructureError, match="RuntimeError"):
        score_ifeval("</think>BLUE", INSTRUCTIONS[:1], prompt="synthetic", registry={"fixture:keyword": BrokenChecker})


@pytest.mark.parametrize("bad", [None, 1, "false"])
def test_non_boolean_instruction_checker_is_rejected(bad: Any) -> None:
    class MalformedChecker(_FixtureInstruction):
        def check_following(self, value: str) -> Any:
            return bad

    with pytest.raises(ScorerInfrastructureError, match="TypeError"):
        score_ifeval("</think>BLUE", INSTRUCTIONS[:1], prompt="synthetic", registry={"fixture:keyword": MalformedChecker})


@pytest.mark.parametrize("finish", ["abort", "error", "timeout", ""])
def test_abnormal_generation_finish_is_never_scored(finish: str) -> None:
    with pytest.raises(ScorerInfrastructureError, match="normally"):
        score_math(r"\boxed{4}", "4", verifier=_fixture_equivalence, finish_reason=finish)
    with pytest.raises(ScorerInfrastructureError, match="normally"):
        score_ifeval("</think>BLUE", INSTRUCTIONS, prompt="synthetic", registry=REGISTRY, finish_reason=finish)


def test_missing_math_answer_stays_in_denominator_without_calling_checker() -> None:
    def unexpected(prediction: str, gold: str) -> bool:
        raise AssertionError("must not check a nonexistent answer")

    assert score_math("unfinished reasoning", "4", verifier=unexpected, finish_reason="length").score == 0


def test_bad_gold_is_infrastructure_failure() -> None:
    with pytest.raises(ScorerInfrastructureError, match="gold"):
        score_math("", " ", verifier=_fixture_equivalence)


def test_math_checker_failure_is_not_silently_zero() -> None:
    def broken(prediction: str, gold: str) -> bool:
        raise TimeoutError("checker timed out")

    with pytest.raises(ScorerInfrastructureError, match="TimeoutError"):
        score_math(r"\boxed{4}", "4", verifier=broken)


def test_non_boolean_math_checker_is_not_silently_true() -> None:
    def broken(prediction: str, gold: str) -> Any:
        return "false"

    with pytest.raises(ScorerInfrastructureError, match="TypeError"):
        score_math(r"\boxed{4}", "4", verifier=broken)


def test_math_verify_missing_dependency_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> Any:
        raise ImportError("not installed")

    monkeypatch.setattr(scorers.importlib, "import_module", missing)
    with pytest.raises(ScorerUnavailableError, match="math-verify"):
        math_verify_equivalent("4", "4")


def test_math_verify_uses_boxed_gold_first_and_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []

    def parse(text: str, *, parsing_timeout: int) -> list[str]:
        calls.append(("parse", text, parsing_timeout))
        return [text]

    def verify(gold: list[str], prediction: list[str], *, timeout_seconds: int) -> bool:
        calls.append(("verify", gold, prediction, timeout_seconds))
        return True

    module = SimpleNamespace(parse=parse, verify=verify)
    monkeypatch.setattr(scorers.importlib, "import_module", lambda _: module)
    assert math_verify_equivalent(r"\frac{1}{2}", "0.5") is True
    assert calls == [
        ("parse", r"\boxed{0.5}", 5),
        ("parse", r"\boxed{\frac{1}{2}}", 5),
        ("verify", [r"\boxed{0.5}"], [r"\boxed{\frac{1}{2}}"], 5),
    ]


def test_math_verify_unparseable_prediction_is_wrong_but_bad_gold_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    module = SimpleNamespace(parse=lambda text, **_: [] if "bad" in text else [text])
    monkeypatch.setattr(scorers.importlib, "import_module", lambda _: module)
    assert math_verify_equivalent("bad", "4") is False
    with pytest.raises(ScorerInfrastructureError, match="gold"):
        math_verify_equivalent("4", "bad")


def test_math_verify_runs_with_effective_timeouts_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scorers.threading, "current_thread", object)
    with pytest.raises(ScorerInfrastructureError, match="main-thread"):
        math_verify_equivalent("4", "4")


def test_grouped_sample_mean_is_not_pass_at_k() -> None:
    fixture = FIXTURES["grouped_accuracy"]
    samples = [
        ScoredSample(problem, str(i), score)
        for problem, scores in fixture["scores"].items()
        for i, score in enumerate(scores)
    ]
    result = grouped_sample_accuracy(samples, samples_per_problem=fixture["samples_per_problem"])
    assert result.mean_accuracy == fixture["expected_mean_accuracy"]
    assert dict(result.problem_scores) == fixture["expected_problem_scores"]
    assert result.n_problems == 2
    assert result.n_samples == 8
    assert result.mean_accuracy != 0.5  # pass@4 would inflate one successful sample to a whole solved problem.


def test_incomplete_or_surplus_samples_are_rejected() -> None:
    samples = [ScoredSample("a", "0", 0), ScoredSample("b", "0", 1), ScoredSample("b", "1", 0)]
    for count in (1, 2):
        with pytest.raises(ValueError, match="incomplete or surplus"):
            grouped_sample_accuracy(samples, samples_per_problem=count)


def test_duplicate_samples_cannot_inflate_evidence() -> None:
    sample = ScoredSample("a", "0", 1)
    with pytest.raises(ValueError, match="duplicate"):
        grouped_sample_accuracy([sample, sample], samples_per_problem=2)


def test_empty_cohort_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        grouped_sample_accuracy([], samples_per_problem=2)


@pytest.mark.parametrize("count", [0, -1, True])
def test_invalid_sample_count_is_rejected(count: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        grouped_sample_accuracy([ScoredSample("a", "0", 0)], samples_per_problem=count)


@pytest.mark.parametrize("score", [-1, 2, True, 0.5])
def test_sample_score_is_binary(score: Any) -> None:
    with pytest.raises(ValueError, match="integer zero or one"):
        ScoredSample("a", "0", score)
