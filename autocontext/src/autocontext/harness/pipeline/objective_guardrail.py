"""Objective verification as binding guardrail for judge-based tasks (AC-325).

Promotes oracle and rubric comparison metrics into the advancement path.
Blocks advance when objective verification fails even if rubric score
improves. Supports forecast-style proper scoring rule settlement.

Key types:
- ObjectiveGuardrailPolicy: configurable thresholds
- GuardrailResult: pass/fail with violations list
- check_objective_guardrail(): threshold check
- ForecastClaim: confidence-bearing verifiable claim
- settle_forecasts(): Brier score settlement for resolved claims
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ObjectiveGuardrailPolicy:
    """Configurable thresholds for objective verification guardrail."""

    min_recall: float = 0.5
    min_precision: float = 0.5
    max_false_positive_rate: float = 0.3
    max_rubric_objective_gap: float = 0.2
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_recall": self.min_recall,
            "min_precision": self.min_precision,
            "max_false_positive_rate": self.max_false_positive_rate,
            "max_rubric_objective_gap": self.max_rubric_objective_gap,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObjectiveGuardrailPolicy:
        return cls(
            min_recall=data.get("min_recall", 0.5),
            min_precision=data.get("min_precision", 0.5),
            max_false_positive_rate=data.get("max_false_positive_rate", 0.3),
            max_rubric_objective_gap=data.get("max_rubric_objective_gap", 0.2),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class GuardrailResult:
    """Outcome of an objective guardrail check."""

    passed: bool
    reason: str
    violations: list[str]
    metrics: dict[str, float]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "violations": self.violations,
            "metrics": self.metrics,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuardrailResult:
        return cls(
            passed=data.get("passed", False),
            reason=data.get("reason", ""),
            violations=data.get("violations", []),
            metrics=data.get("metrics", {}),
            metadata=data.get("metadata", {}),
        )


def check_objective_guardrail(
    *,
    recall: float,
    precision: float,
    false_positive_rate: float,
    rubric_score: float,
    objective_recall: float,
    policy: ObjectiveGuardrailPolicy,
) -> GuardrailResult:
    """Check objective verification metrics against policy thresholds."""
    if not policy.enabled:
        return GuardrailResult(
            passed=True,
            reason="Objective guardrail disabled",
            violations=[],
            metrics={"recall": recall, "precision": precision},
        )

    violations: list[str] = []
    metrics = {
        "recall": recall,
        "precision": precision,
        "false_positive_rate": false_positive_rate,
        "rubric_score": rubric_score,
        "objective_recall": objective_recall,
    }

    if recall < policy.min_recall:
        violations.append(f"recall {recall:.4f} < min {policy.min_recall:.4f}")

    if precision < policy.min_precision:
        violations.append(f"precision {precision:.4f} < min {policy.min_precision:.4f}")

    if false_positive_rate > policy.max_false_positive_rate:
        violations.append(
            f"false positive rate {false_positive_rate:.4f} > max {policy.max_false_positive_rate:.4f}"
        )

    gap = abs(rubric_score - objective_recall)
    metrics["rubric_objective_gap"] = gap
    if gap > policy.max_rubric_objective_gap:
        violations.append(
            f"rubric-objective gap {gap:.4f} > max {policy.max_rubric_objective_gap:.4f}"
        )

    if violations:
        return GuardrailResult(
            passed=False,
            reason=f"{len(violations)} threshold violation(s)",
            violations=violations,
            metrics=metrics,
        )

    return GuardrailResult(
        passed=True,
        reason="All objective thresholds met",
        violations=[],
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Forecast-style proper scoring rule support
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ForecastClaim:
    """A confidence-bearing verifiable claim."""

    claim_id: str
    description: str
    confidence: float  # 0.0 to 1.0 — agent's stated probability
    resolved: bool
    ground_truth: bool | None  # None if not yet resolved


def settle_forecasts(claims: list[ForecastClaim]) -> dict[str, Any]:
    """Settle resolved forecast claims using Brier score.

    Brier score = mean((confidence - outcome)^2) for resolved claims.
    Lower is better (0.0 = perfect calibration).
    """
    resolved = [c for c in claims if c.resolved and c.ground_truth is not None]
    pending = [c for c in claims if not c.resolved]

    if not resolved:
        return {
            "brier_score": 0.0,
            "num_resolved": 0,
            "num_pending": len(pending),
        }

    brier_sum = sum(
        (c.confidence - (1.0 if c.ground_truth else 0.0)) ** 2
        for c in resolved
    )
    brier_score = round(brier_sum / len(resolved), 6)

    return {
        "brier_score": brier_score,
        "num_resolved": len(resolved),
        "num_pending": len(pending),
    }
