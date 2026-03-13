"""AC-196: Weakness reports — Phase 1 analysis of recurring failure patterns.

Analyzes generation trajectory and match data to identify weaknesses:
score regressions, validation failures, match variance, stagnation risk,
and dead-end patterns. Produces structured, human-readable reports.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger(__name__)

WEAKNESS_CATEGORIES = frozenset({
    "score_regression",
    "validation_failure",
    "match_variance",
    "stagnation_risk",
    "dead_end_pattern",
})


@dataclass(slots=True)
class Weakness:
    """A single identified weakness with evidence."""

    category: str
    severity: str  # "high", "medium", "low"
    affected_generations: list[int]
    description: str
    evidence: dict[str, Any]
    frequency: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "affected_generations": self.affected_generations,
            "description": self.description,
            "evidence": self.evidence,
            "frequency": self.frequency,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Weakness:
        return cls(
            category=str(data.get("category", "")),
            severity=str(data.get("severity", "low")),
            affected_generations=list(data.get("affected_generations", [])),
            description=str(data.get("description", "")),
            evidence=dict(data.get("evidence", {})),
            frequency=int(data.get("frequency", 0)),
        )


@dataclass(slots=True)
class WeaknessReport:
    """Structured weakness report for a run."""

    run_id: str
    scenario: str
    total_generations: int
    weaknesses: list[Weakness] = field(default_factory=list)

    @property
    def high_severity_count(self) -> int:
        return sum(1 for w in self.weaknesses if w.severity == "high")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "total_generations": self.total_generations,
            "weaknesses": [w.to_dict() for w in self.weaknesses],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeaknessReport:
        return cls(
            run_id=str(data.get("run_id", "")),
            scenario=str(data.get("scenario", "")),
            total_generations=int(data.get("total_generations", 0)),
            weaknesses=[Weakness.from_dict(w) for w in data.get("weaknesses", [])],
        )

    def to_markdown(self) -> str:
        lines = [
            f"# Weakness Report: {self.run_id}",
            f"**Scenario:** {self.scenario} | **Generations:** {self.total_generations}",
            "",
        ]
        if not self.weaknesses:
            lines.append("No weaknesses identified.")
            return "\n".join(lines)

        high = [w for w in self.weaknesses if w.severity == "high"]
        medium = [w for w in self.weaknesses if w.severity == "medium"]
        low = [w for w in self.weaknesses if w.severity == "low"]

        lines.append(f"**Summary:** {len(self.weaknesses)} weaknesses "
                      f"({len(high)} high, {len(medium)} medium, {len(low)} low)")
        lines.append("")

        for weakness in self.weaknesses:
            severity_tag = weakness.severity.upper()
            lines.append(f"## [{severity_tag}] {weakness.category}")
            lines.append(f"{weakness.description}")
            if weakness.affected_generations:
                gens = ", ".join(str(g) for g in weakness.affected_generations)
                lines.append(f"- Affected generations: {gens}")
            if weakness.frequency:
                lines.append(f"- Frequency: {weakness.frequency}")
            if weakness.evidence:
                for key, val in weakness.evidence.items():
                    lines.append(f"- {key}: {val}")
            lines.append("")

        return "\n".join(lines)


def _safe_float(val: Any, default: float = 0.0) -> float:  # noqa: ANN401
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:  # noqa: ANN401
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


class WeaknessAnalyzer:
    """Analyzes generation trajectory and match data for recurring failure patterns."""

    def __init__(
        self,
        *,
        regression_threshold: int = 2,
        validation_failure_threshold: int = 2,
        variance_threshold: float = 0.15,
        consecutive_rollback_threshold: int = 3,
        dead_end_threshold: int = 3,
    ) -> None:
        self._regression_threshold = regression_threshold
        self._validation_failure_threshold = validation_failure_threshold
        self._variance_threshold = variance_threshold
        self._consecutive_rollback_threshold = consecutive_rollback_threshold
        self._dead_end_threshold = dead_end_threshold

    def analyze(
        self,
        *,
        run_id: str,
        scenario: str,
        trajectory: list[dict[str, Any]],
        match_data: list[dict[str, Any]] | None = None,
    ) -> WeaknessReport:
        if not trajectory:
            return WeaknessReport(run_id=run_id, scenario=scenario, total_generations=0)

        weaknesses: list[Weakness] = []

        weaknesses.extend(self._detect_score_regression(trajectory))
        weaknesses.extend(self._detect_stagnation_risk(trajectory))
        weaknesses.extend(self._detect_dead_end_pattern(trajectory))

        if match_data:
            weaknesses.extend(self._detect_validation_failures(match_data))
            weaknesses.extend(self._detect_match_variance(match_data))

        return WeaknessReport(
            run_id=run_id,
            scenario=scenario,
            total_generations=len(trajectory),
            weaknesses=weaknesses,
        )

    def _detect_score_regression(self, trajectory: list[dict[str, Any]]) -> list[Weakness]:
        regression_gens: list[int] = []
        deltas: list[float] = []
        for row in trajectory:
            delta = _safe_float(row.get("delta", 0))
            decision = str(row.get("gate_decision", ""))
            if decision == "rollback" and delta < 0:
                regression_gens.append(_safe_int(row.get("generation_index", 0)))
                deltas.append(delta)

        if len(regression_gens) >= self._regression_threshold:
            worst = min(deltas)
            avg = sum(deltas) / len(deltas)
            return [Weakness(
                category="score_regression",
                severity="high" if len(regression_gens) >= 3 else "medium",
                affected_generations=regression_gens,
                description=f"Score regressed in {len(regression_gens)} generations with rollback",
                evidence={"delta_avg": round(avg, 4), "worst_delta": round(worst, 4)},
                frequency=len(regression_gens),
            )]
        return []

    def _detect_validation_failures(self, match_data: list[dict[str, Any]]) -> list[Weakness]:
        failed_gens: set[int] = set()
        error_types: dict[str, int] = {}
        total_failures = 0

        for match in match_data:
            if not match.get("passed_validation", True):
                total_failures += 1
                gen = _safe_int(match.get("generation_index", 0))
                failed_gens.add(gen)
                raw_errors = match.get("validation_errors", "[]")
                if isinstance(raw_errors, str):
                    try:
                        errors = json.loads(raw_errors)
                    except (json.JSONDecodeError, TypeError):
                        errors = []
                else:
                    errors = raw_errors if isinstance(raw_errors, list) else []
                for err in errors:
                    err_str = str(err)
                    error_types[err_str] = error_types.get(err_str, 0) + 1

        if total_failures >= self._validation_failure_threshold:
            return [Weakness(
                category="validation_failure",
                severity="high" if total_failures >= 5 else "medium",
                affected_generations=sorted(failed_gens),
                description=f"Validation failures in {total_failures} matches across {len(failed_gens)} generations",
                evidence={"error_types": error_types, "total_failures": total_failures},
                frequency=total_failures,
            )]
        return []

    def _detect_match_variance(self, match_data: list[dict[str, Any]]) -> list[Weakness]:
        by_gen: dict[int, list[float]] = {}
        for match in match_data:
            gen = _safe_int(match.get("generation_index", 0))
            score = _safe_float(match.get("score", 0))
            by_gen.setdefault(gen, []).append(score)

        high_variance_gens: list[int] = []
        worst_std = 0.0
        for gen, scores in by_gen.items():
            if len(scores) < 2:
                continue
            mean = sum(scores) / len(scores)
            variance = sum((s - mean) ** 2 for s in scores) / len(scores)
            std = math.sqrt(variance)
            if std > self._variance_threshold:
                high_variance_gens.append(gen)
                worst_std = max(worst_std, std)

        if high_variance_gens:
            return [Weakness(
                category="match_variance",
                severity="medium" if worst_std < 0.3 else "high",
                affected_generations=sorted(high_variance_gens),
                description=f"High score variance across matches in {len(high_variance_gens)} generations",
                evidence={"worst_std_dev": round(worst_std, 4)},
                frequency=len(high_variance_gens),
            )]
        return []

    def _detect_stagnation_risk(self, trajectory: list[dict[str, Any]]) -> list[Weakness]:
        consecutive_rollbacks = 0
        max_streak = 0
        streak_gens: list[int] = []
        current_streak_gens: list[int] = []

        for row in trajectory:
            if str(row.get("gate_decision", "")) == "rollback":
                consecutive_rollbacks += 1
                current_streak_gens.append(_safe_int(row.get("generation_index", 0)))
                if consecutive_rollbacks > max_streak:
                    max_streak = consecutive_rollbacks
                    streak_gens = list(current_streak_gens)
            else:
                consecutive_rollbacks = 0
                current_streak_gens = []

        if max_streak >= self._consecutive_rollback_threshold:
            return [Weakness(
                category="stagnation_risk",
                severity="high" if max_streak >= 5 else "medium",
                affected_generations=streak_gens,
                description=f"{max_streak} consecutive rollbacks indicate stagnation risk",
                evidence={"max_consecutive_rollbacks": max_streak},
                frequency=max_streak,
            )]
        return []

    def _detect_dead_end_pattern(self, trajectory: list[dict[str, Any]]) -> list[Weakness]:
        rollback_gens: list[int] = []
        for row in trajectory:
            if str(row.get("gate_decision", "")) == "rollback":
                rollback_gens.append(_safe_int(row.get("generation_index", 0)))

        if len(rollback_gens) >= self._dead_end_threshold:
            ratio = len(rollback_gens) / len(trajectory)
            return [Weakness(
                category="dead_end_pattern",
                severity="high" if ratio > 0.5 else "medium",
                affected_generations=rollback_gens,
                description=f"{len(rollback_gens)} of {len(trajectory)} generations rolled back",
                evidence={"rollback_ratio": round(ratio, 4)},
                frequency=len(rollback_gens),
            )]
        return []
