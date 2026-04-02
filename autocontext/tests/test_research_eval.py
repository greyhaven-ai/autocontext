"""Tests for research A/B evaluation (AC-502).

DDD: ResearchEvaluator compares research-augmented vs baseline outputs,
producing structured evaluation results for quality gating.
"""

from __future__ import annotations

import pytest

from autocontext.research.consultation import ResearchBrief
from autocontext.research.types import Citation, ResearchResult


def _brief(n: int = 1, confidence: float = 0.8) -> ResearchBrief:
    results = [
        ResearchResult(
            query_topic=f"topic-{i}",
            summary=f"Finding {i}",
            confidence=confidence,
            citations=[Citation(source=f"src-{i}", url=f"https://ex.com/{i}", relevance=0.9)],
        )
        for i in range(n)
    ]
    return ResearchBrief.from_results(goal="test", results=results)


class TestEvalResult:
    def test_create_eval_result(self) -> None:
        from autocontext.research.evaluation import EvalResult

        r = EvalResult(
            baseline_score=0.6,
            augmented_score=0.85,
            improvement=0.25,
            citation_coverage=0.9,
            sample_size=10,
        )
        assert r.is_improvement
        assert r.relative_gain == pytest.approx(0.4167, abs=0.01)

    def test_no_improvement(self) -> None:
        from autocontext.research.evaluation import EvalResult

        r = EvalResult(baseline_score=0.8, augmented_score=0.75, improvement=-0.05)
        assert not r.is_improvement

    def test_zero_baseline_gain(self) -> None:
        from autocontext.research.evaluation import EvalResult

        r = EvalResult(baseline_score=0.0, augmented_score=0.5, improvement=0.5)
        assert r.relative_gain == float("inf")


class TestResearchEvaluator:
    def test_evaluate_pair(self) -> None:
        from autocontext.research.evaluation import ResearchEvaluator

        evaluator = ResearchEvaluator()
        result = evaluator.evaluate_pair(
            brief=_brief(),
            baseline_output="Generic auth answer",
            augmented_output="OAuth2 with PKCE flow as recommended by RFC 7636",
            score_fn=lambda text: 0.9 if "RFC" in text else 0.5,
        )
        assert result.is_improvement
        assert result.augmented_score > result.baseline_score

    def test_evaluate_pair_no_improvement(self) -> None:
        from autocontext.research.evaluation import ResearchEvaluator

        evaluator = ResearchEvaluator()
        result = evaluator.evaluate_pair(
            brief=_brief(),
            baseline_output="Great answer",
            augmented_output="Also great answer",
            score_fn=lambda text: 0.8,
        )
        assert not result.is_improvement
        assert result.improvement == pytest.approx(0.0)

    def test_evaluate_batch(self) -> None:
        from autocontext.research.evaluation import ResearchEvaluator

        evaluator = ResearchEvaluator()
        pairs = [
            {
                "brief": _brief(),
                "baseline": "basic",
                "augmented": "research-backed with RFC citation",
            },
            {
                "brief": _brief(),
                "baseline": "generic",
                "augmented": "detailed with RFC source",
            },
        ]
        summary = evaluator.evaluate_batch(
            pairs=pairs,
            score_fn=lambda text: 0.9 if "RFC" in text else 0.5,
        )
        assert summary.sample_size == 2
        assert summary.avg_improvement > 0
        assert summary.win_rate == pytest.approx(1.0)

    def test_evaluate_batch_empty(self) -> None:
        from autocontext.research.evaluation import ResearchEvaluator

        evaluator = ResearchEvaluator()
        summary = evaluator.evaluate_batch(pairs=[], score_fn=lambda t: 0.5)
        assert summary.sample_size == 0

    def test_citation_coverage(self) -> None:
        from autocontext.research.evaluation import ResearchEvaluator

        evaluator = ResearchEvaluator()
        brief = _brief(n=2)
        result = evaluator.evaluate_pair(
            brief=brief,
            baseline_output="no citations",
            augmented_output="According to src-0 and src-1, the approach is solid",
            score_fn=lambda t: 0.7,
        )
        assert result.citation_coverage == pytest.approx(1.0)

    def test_partial_citation_coverage(self) -> None:
        from autocontext.research.evaluation import ResearchEvaluator

        evaluator = ResearchEvaluator()
        brief = _brief(n=3)
        result = evaluator.evaluate_pair(
            brief=brief,
            baseline_output="none",
            augmented_output="Only src-0 was referenced",
            score_fn=lambda t: 0.7,
        )
        assert result.citation_coverage == pytest.approx(1 / 3, abs=0.01)

    def test_citation_coverage_avoids_prefix_false_positives(self) -> None:
        from autocontext.research.evaluation import ResearchEvaluator

        evaluator = ResearchEvaluator()
        brief = ResearchBrief.from_results(
            goal="test",
            results=[
                ResearchResult(
                    query_topic="topic",
                    summary="Finding",
                    confidence=0.8,
                    citations=[Citation(source="src-1", relevance=0.9)],
                )
            ],
        )
        result = evaluator.evaluate_pair(
            brief=brief,
            baseline_output="none",
            augmented_output="This only mentions src-10, not the cited source",
            score_fn=lambda t: 0.7,
        )
        assert result.citation_coverage == pytest.approx(0.0)
