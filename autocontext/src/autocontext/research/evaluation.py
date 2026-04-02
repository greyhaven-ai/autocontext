"""Research A/B evaluation — compare augmented vs baseline (AC-502).

Domain service: ResearchEvaluator pairs baseline and research-augmented
outputs, scores them with a pluggable score function, and measures
improvement and citation coverage.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from autocontext.research.consultation import ResearchBrief

logger = logging.getLogger(__name__)

ScoreFn = Callable[[str], float]


class EvalResult(BaseModel):
    """Result of comparing one baseline/augmented pair."""

    baseline_score: float = 0.0
    augmented_score: float = 0.0
    improvement: float = 0.0
    citation_coverage: float = 0.0
    sample_size: int = 1

    model_config = {"frozen": True}

    @property
    def is_improvement(self) -> bool:
        return self.improvement > 0

    @property
    def relative_gain(self) -> float:
        if self.baseline_score == 0.0:
            return float("inf") if self.improvement > 0 else 0.0
        return self.improvement / self.baseline_score


class BatchSummary(BaseModel):
    """Aggregated summary over multiple eval pairs."""

    sample_size: int = 0
    avg_baseline: float = 0.0
    avg_augmented: float = 0.0
    avg_improvement: float = 0.0
    win_rate: float = 0.0  # fraction where augmented > baseline

    model_config = {"frozen": True}


def _citation_coverage(brief: ResearchBrief, text: str) -> float:
    """Fraction of unique citation sources mentioned in text."""
    if not brief.unique_citations:
        return 0.0
    mentioned = sum(1 for c in brief.unique_citations if _source_mentioned(c.source, text))
    return mentioned / len(brief.unique_citations)


def _source_mentioned(source: str, text: str) -> bool:
    """Return True when `source` appears as a distinct mention in `text`."""
    pattern = rf"(?<!\w){re.escape(source)}(?!\w)"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


class ResearchEvaluator:
    """Compares research-augmented vs baseline outputs."""

    def evaluate_pair(
        self,
        brief: ResearchBrief,
        baseline_output: str,
        augmented_output: str,
        score_fn: ScoreFn,
    ) -> EvalResult:
        baseline_score = score_fn(baseline_output)
        augmented_score = score_fn(augmented_output)
        return EvalResult(
            baseline_score=baseline_score,
            augmented_score=augmented_score,
            improvement=augmented_score - baseline_score,
            citation_coverage=_citation_coverage(brief, augmented_output),
        )

    def evaluate_batch(
        self,
        pairs: Sequence[dict[str, Any]],
        score_fn: ScoreFn,
    ) -> BatchSummary:
        if not pairs:
            return BatchSummary()

        results: list[EvalResult] = []
        for p in pairs:
            r = self.evaluate_pair(
                brief=p["brief"],
                baseline_output=p["baseline"],
                augmented_output=p["augmented"],
                score_fn=score_fn,
            )
            results.append(r)

        n = len(results)
        wins = sum(1 for r in results if r.is_improvement)
        return BatchSummary(
            sample_size=n,
            avg_baseline=sum(r.baseline_score for r in results) / n,
            avg_augmented=sum(r.augmented_score for r in results) / n,
            avg_improvement=sum(r.improvement for r in results) / n,
            win_rate=wins / n,
        )
