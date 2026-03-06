"""Structured failure reports for enriched retry context.

Inspired by Plankton's normalized violation JSON that gives subprocess agents
actionable context about what went wrong and how to fix it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from mts.harness.evaluation.types import EvaluationSummary


@dataclass(frozen=True, slots=True)
class MatchDiagnosis:
    """Per-match analysis of a tournament result."""

    match_index: int
    score: float
    passed: bool
    errors: list[str]
    summary: str


@dataclass(frozen=True, slots=True)
class FailureReport:
    """Structured failure analysis for enriched retry context."""

    match_diagnoses: list[MatchDiagnosis]
    overall_delta: float
    threshold: float
    previous_best: float
    current_best: float
    strategy_summary: str

    @classmethod
    def from_tournament(
        cls,
        tournament: EvaluationSummary,
        *,
        previous_best: float,
        threshold: float,
        strategy: dict,
    ) -> FailureReport:
        """Build a failure report from tournament results."""
        diagnoses: list[MatchDiagnosis] = []
        for i, result in enumerate(tournament.results):
            diagnoses.append(MatchDiagnosis(
                match_index=i,
                score=result.score,
                passed=result.passed,
                errors=list(result.errors),
                summary=f"Match {i}: score={result.score:.4f}, passed={result.passed}",
            ))
        delta = round(tournament.best_score - previous_best, 6)
        full_json = json.dumps(strategy, sort_keys=True)
        strategy_str = full_json if len(full_json) <= 200 else full_json[:200] + "..."
        return cls(
            match_diagnoses=diagnoses,
            overall_delta=delta,
            threshold=threshold,
            previous_best=previous_best,
            current_best=tournament.best_score,
            strategy_summary=strategy_str,
        )

    def to_prompt_context(self) -> str:
        """Format the failure report as structured prompt context for retry."""
        lines = [
            "--- FAILURE ANALYSIS ---",
            f"Previous best: {self.previous_best:.4f}",
            f"Current best:  {self.current_best:.4f}",
            f"Delta: {self.overall_delta:+.6f} (needed >= {self.threshold})",
            f"Strategy: {self.strategy_summary}",
            "",
            "Per-match results:",
        ]
        for d in self.match_diagnoses:
            error_str = f" errors={d.errors}" if d.errors else ""
            lines.append(f"  Match {d.match_index}: score={d.score:.4f}{error_str}")
        lines.append("")
        lines.append("Adjust your strategy to improve across ALL matches. Do not repeat the same approach.")
        return "\n".join(lines)
