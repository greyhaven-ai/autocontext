"""Harness quality signal computation for Curator evaluation (MTS-93)."""
from __future__ import annotations

from dataclasses import dataclass

from autocontext.harness.evaluation.types import EvaluationResult


@dataclass(slots=True)
class HarnessQualitySignal:
    """Quality metrics derived from tournament match results."""

    total_matches: int
    error_count: int
    crash_count: int

    @property
    def error_rate(self) -> float:
        """Fraction of matches that had errors (0.0–1.0)."""
        if self.total_matches == 0:
            return 0.0
        return self.error_count / self.total_matches

    @property
    def crash_rate(self) -> float:
        """Fraction of matches that crashed (0.0–1.0)."""
        if self.total_matches == 0:
            return 0.0
        return self.crash_count / self.total_matches

    def to_prompt_section(self, previous: HarnessQualitySignal | None = None) -> str:
        """Format as markdown section for Curator prompt."""
        lines = [
            "## Harness Quality",
            f"- Error rate: {self.error_rate:.0%} ({self.error_count}/{self.total_matches} matches)",
            f"- Crash rate: {self.crash_rate:.0%} ({self.crash_count}/{self.total_matches} matches)",
        ]
        if previous is not None:
            delta_err = self.error_rate - previous.error_rate
            delta_crash = self.crash_rate - previous.crash_rate
            direction_err = "improved" if delta_err < 0 else ("worse" if delta_err > 0 else "unchanged")
            direction_crash = "improved" if delta_crash < 0 else ("worse" if delta_crash > 0 else "unchanged")
            lines.append(f"- Error trend: {direction_err} (was {previous.error_rate:.0%})")
            lines.append(f"- Crash trend: {direction_crash} (was {previous.crash_rate:.0%})")
        lines.append("")
        return "\n".join(lines)


def compute_harness_quality(results: list[EvaluationResult]) -> HarnessQualitySignal:
    """Compute quality signal from a list of match EvaluationResults."""
    error_count = 0
    crash_count = 0
    for r in results:
        if r.errors:
            error_count += 1
        if not r.passed:
            crash_count += 1
    return HarnessQualitySignal(
        total_matches=len(results),
        error_count=error_count,
        crash_count=crash_count,
    )
