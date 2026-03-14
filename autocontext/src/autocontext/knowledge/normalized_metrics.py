"""AC-190: Normalized cross-scenario progress and cost-efficiency reporting.

Maps native scenario scores to a consistent [0, 1] reporting scale and
computes cost-efficiency metrics (tokens per advance, cost per score point).
Purely for operator review — not used for backpressure or gating decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autocontext.harness.core.types import RoleUsage
from autocontext.harness.cost.calculator import CostCalculator


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


# ---------------------------------------------------------------------------
# NormalizedProgress
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class NormalizedProgress:
    """A score mapped to a consistent [0, 1] reporting scale."""

    raw_score: float
    normalized_score: float
    score_floor: float = 0.0
    score_ceiling: float = 1.0
    pct_of_ceiling: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_score": self.raw_score,
            "normalized_score": self.normalized_score,
            "score_floor": self.score_floor,
            "score_ceiling": self.score_ceiling,
            "pct_of_ceiling": self.pct_of_ceiling,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NormalizedProgress:
        return cls(
            raw_score=_safe_float(data.get("raw_score")),
            normalized_score=_safe_float(data.get("normalized_score")),
            score_floor=_safe_float(data.get("score_floor")),
            score_ceiling=_safe_float(data.get("score_ceiling", 1.0), 1.0),
            pct_of_ceiling=_safe_float(data.get("pct_of_ceiling")),
        )


# ---------------------------------------------------------------------------
# CostEfficiency
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CostEfficiency:
    """Token and cost efficiency metrics for a run."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    tokens_per_advance: int = 0
    cost_per_advance: float = 0.0
    tokens_per_score_point: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "tokens_per_advance": self.tokens_per_advance,
            "cost_per_advance": self.cost_per_advance,
            "tokens_per_score_point": self.tokens_per_score_point,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostEfficiency:
        return cls(
            total_input_tokens=_safe_int(data.get("total_input_tokens")),
            total_output_tokens=_safe_int(data.get("total_output_tokens")),
            total_tokens=_safe_int(data.get("total_tokens")),
            total_cost_usd=_safe_float(data.get("total_cost_usd")),
            tokens_per_advance=_safe_int(data.get("tokens_per_advance")),
            cost_per_advance=_safe_float(data.get("cost_per_advance")),
            tokens_per_score_point=_safe_int(data.get("tokens_per_score_point")),
        )


# ---------------------------------------------------------------------------
# ScenarioNormalizer
# ---------------------------------------------------------------------------

class ScenarioNormalizer:
    """Maps native scenario scores to [0, 1] range."""

    def __init__(self, score_floor: float = 0.0, score_ceiling: float = 1.0) -> None:
        self.score_floor = score_floor
        self.score_ceiling = score_ceiling

    def normalize(self, raw_score: float) -> NormalizedProgress:
        span = self.score_ceiling - self.score_floor
        if span <= 0:
            return NormalizedProgress(
                raw_score=raw_score,
                normalized_score=0.0,
                score_floor=self.score_floor,
                score_ceiling=self.score_ceiling,
                pct_of_ceiling=0.0,
            )
        clamped = max(self.score_floor, min(raw_score, self.score_ceiling))
        normalized = (clamped - self.score_floor) / span
        return NormalizedProgress(
            raw_score=raw_score,
            normalized_score=normalized,
            score_floor=self.score_floor,
            score_ceiling=self.score_ceiling,
            pct_of_ceiling=round(normalized * 100, 2),
        )


# ---------------------------------------------------------------------------
# RunProgressReport
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RunProgressReport:
    """Per-run normalized progress and cost-efficiency report."""

    run_id: str
    scenario: str
    total_generations: int
    advances: int
    rollbacks: int
    retries: int
    progress: NormalizedProgress
    cost: CostEfficiency
    annotations: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "total_generations": self.total_generations,
            "advances": self.advances,
            "rollbacks": self.rollbacks,
            "retries": self.retries,
            "progress": self.progress.to_dict(),
            "cost": self.cost.to_dict(),
            "annotations": self.annotations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunProgressReport:
        return cls(
            run_id=str(data.get("run_id", "")),
            scenario=str(data.get("scenario", "")),
            total_generations=_safe_int(data.get("total_generations")),
            advances=_safe_int(data.get("advances")),
            rollbacks=_safe_int(data.get("rollbacks")),
            retries=_safe_int(data.get("retries")),
            progress=NormalizedProgress.from_dict(data.get("progress", {})),
            cost=CostEfficiency.from_dict(data.get("cost", {})),
            annotations=dict(data.get("annotations", {})),
        )

    def to_markdown(self) -> str:
        lines = [
            f"# Progress Report: {self.run_id}",
            f"**Scenario:** {self.scenario} | **Generations:** {self.total_generations}",
            "",
            "## Progress",
            f"- Score: {self.progress.raw_score:.4f} ({self.progress.pct_of_ceiling}% of ceiling)",
            f"- Normalized: {self.progress.normalized_score:.4f}",
            f"- Score range: [{self.progress.score_floor}, {self.progress.score_ceiling}]",
            "",
            "## Gate Decisions",
            f"- Advances: {self.advances}",
            f"- Rollbacks: {self.rollbacks}",
            f"- Retries: {self.retries}",
            "",
            "## Cost Efficiency",
            f"- Total tokens: {self.cost.total_tokens:,}",
            f"- Input / Output: {self.cost.total_input_tokens:,} / {self.cost.total_output_tokens:,}",
            f"- Total cost: ${self.cost.total_cost_usd:.4f}",
        ]
        if self.cost.tokens_per_advance:
            lines.append(f"- Tokens per advance: {self.cost.tokens_per_advance:,}")
        if self.cost.cost_per_advance:
            lines.append(f"- Cost per advance: ${self.cost.cost_per_advance:.4f}")
        if self.cost.tokens_per_score_point:
            lines.append(f"- Tokens per score point: {self.cost.tokens_per_score_point:,}")
        lines.append("")

        if self.annotations:
            lines.append("## Annotations")
            for key, val in self.annotations.items():
                lines.append(f"- **{key}**: {val}")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def compute_normalized_progress(
    trajectory: list[dict[str, Any]],
    *,
    normalizer: ScenarioNormalizer | None = None,
) -> NormalizedProgress:
    """Compute normalized progress from trajectory rows."""
    if normalizer is None:
        normalizer = ScenarioNormalizer()
    if not trajectory:
        return normalizer.normalize(0.0)
    last_best = _safe_float(trajectory[-1].get("best_score", 0))
    return normalizer.normalize(last_best)


def compute_cost_efficiency(
    *,
    role_metrics: list[dict[str, Any]],
    trajectory: list[dict[str, Any]],
    consultation_cost: float = 0.0,
) -> CostEfficiency:
    """Compute cost-efficiency metrics from role metrics and trajectory."""
    total_in = sum(_safe_int(m.get("input_tokens")) for m in role_metrics)
    total_out = sum(_safe_int(m.get("output_tokens")) for m in role_metrics)
    total_tokens = total_in + total_out

    advances = sum(
        1 for row in trajectory
        if str(row.get("gate_decision", "")) == "advance"
    )

    tokens_per_advance = total_tokens // advances if advances > 0 else 0

    calculator = CostCalculator()
    total_cost = consultation_cost
    for metric in role_metrics:
        record = calculator.from_usage(
            RoleUsage(
                input_tokens=_safe_int(metric.get("input_tokens")),
                output_tokens=_safe_int(metric.get("output_tokens")),
                latency_ms=_safe_int(metric.get("latency_ms")),
                model=str(metric.get("model") or "_default"),
            )
        )
        total_cost += record.total_cost

    cost_per_advance = total_cost / advances if advances > 0 else 0.0

    # Net score gain from trajectory
    if len(trajectory) >= 1:
        first_score = _safe_float(trajectory[0].get("best_score", 0))
        first_delta = _safe_float(trajectory[0].get("delta", 0))
        start_score = first_score - first_delta
        last_score = _safe_float(trajectory[-1].get("best_score", 0))
        net_gain = last_score - start_score
    else:
        net_gain = 0.0

    tokens_per_score_point = int(total_tokens / net_gain) if net_gain > 0 else 0

    return CostEfficiency(
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_tokens=total_tokens,
        total_cost_usd=round(total_cost, 6),
        tokens_per_advance=tokens_per_advance,
        cost_per_advance=round(cost_per_advance, 4),
        tokens_per_score_point=tokens_per_score_point,
    )


def generate_run_progress_report(
    *,
    run_id: str,
    scenario: str,
    trajectory: list[dict[str, Any]],
    role_metrics: list[dict[str, Any]],
    normalizer: ScenarioNormalizer | None = None,
    consultation_cost: float = 0.0,
) -> RunProgressReport:
    """Generate a RunProgressReport from raw trajectory and role metrics data."""
    progress = compute_normalized_progress(trajectory, normalizer=normalizer)
    cost = compute_cost_efficiency(
        role_metrics=role_metrics,
        trajectory=trajectory,
        consultation_cost=consultation_cost,
    )

    gate_counts: dict[str, int] = {}
    for row in trajectory:
        decision = str(row.get("gate_decision", "unknown"))
        gate_counts[decision] = gate_counts.get(decision, 0) + 1

    return RunProgressReport(
        run_id=run_id,
        scenario=scenario,
        total_generations=len(trajectory),
        advances=gate_counts.get("advance", 0),
        rollbacks=gate_counts.get("rollback", 0),
        retries=gate_counts.get("retry", 0),
        progress=progress,
        cost=cost,
    )
