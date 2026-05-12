from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from autocontext.knowledge.context_selection import ContextSelectionDecision


@dataclass(frozen=True)
class ContextSelectionStageSummary:
    run_id: str
    scenario_name: str
    generation: int
    stage: str
    created_at: str
    candidate_count: int
    selected_count: int
    candidate_token_estimate: int
    selected_token_estimate: int
    selection_rate: float
    duplicate_content_rate: float
    useful_artifact_recall: float | None
    mean_selected_freshness_generation_delta: float | None

    @classmethod
    def from_decision(cls, decision: ContextSelectionDecision) -> ContextSelectionStageSummary:
        metrics = decision.metrics()
        return cls(
            run_id=decision.run_id,
            scenario_name=decision.scenario_name,
            generation=decision.generation,
            stage=decision.stage,
            created_at=decision.created_at,
            candidate_count=_int_metric(metrics, "candidate_count"),
            selected_count=_int_metric(metrics, "selected_count"),
            candidate_token_estimate=_int_metric(metrics, "candidate_token_estimate"),
            selected_token_estimate=_int_metric(metrics, "selected_token_estimate"),
            selection_rate=_float_metric(metrics, "selection_rate"),
            duplicate_content_rate=_float_metric(metrics, "duplicate_content_rate"),
            useful_artifact_recall=_optional_float_metric(metrics, "useful_artifact_recall"),
            mean_selected_freshness_generation_delta=_optional_float_metric(
                metrics,
                "mean_selected_freshness_generation_delta",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_name": self.scenario_name,
            "generation": self.generation,
            "stage": self.stage,
            "created_at": self.created_at,
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "candidate_token_estimate": self.candidate_token_estimate,
            "selected_token_estimate": self.selected_token_estimate,
            "selection_rate": self.selection_rate,
            "duplicate_content_rate": self.duplicate_content_rate,
            "useful_artifact_recall": self.useful_artifact_recall,
            "mean_selected_freshness_generation_delta": self.mean_selected_freshness_generation_delta,
        }


@dataclass(frozen=True)
class ContextSelectionReport:
    run_id: str
    scenario_name: str
    stages: tuple[ContextSelectionStageSummary, ...]

    def summary(self) -> dict[str, Any]:
        candidate_count = sum(stage.candidate_count for stage in self.stages)
        selected_count = sum(stage.selected_count for stage in self.stages)
        candidate_tokens = sum(stage.candidate_token_estimate for stage in self.stages)
        selected_tokens = sum(stage.selected_token_estimate for stage in self.stages)
        return {
            "candidate_count": candidate_count,
            "selected_count": selected_count,
            "candidate_token_estimate": candidate_tokens,
            "selected_token_estimate": selected_tokens,
            "selection_rate": selected_count / candidate_count if candidate_count else 0.0,
            "mean_selection_rate": _mean(stage.selection_rate for stage in self.stages),
            "mean_duplicate_content_rate": _mean(stage.duplicate_content_rate for stage in self.stages),
            "mean_selected_token_estimate": selected_tokens / len(self.stages) if self.stages else 0.0,
            "max_selected_token_estimate": max(
                (stage.selected_token_estimate for stage in self.stages),
                default=0,
            ),
            "mean_useful_artifact_recall": _mean_optional(stage.useful_artifact_recall for stage in self.stages),
            "mean_selected_freshness_generation_delta": _mean_optional(
                stage.mean_selected_freshness_generation_delta for stage in self.stages
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        generations = {stage.generation for stage in self.stages}
        return {
            "status": "completed",
            "run_id": self.run_id,
            "scenario_name": self.scenario_name,
            "decision_count": len(self.stages),
            "generation_count": len(generations),
            "summary": self.summary(),
            "stages": [stage.to_dict() for stage in self.stages],
        }

    def to_markdown(self) -> str:
        summary = self.summary()
        lines = [
            f"# Context Selection Report: {self.run_id}",
            "",
            f"- Scenario: {self.scenario_name}",
            f"- Decisions: {len(self.stages)}",
            f"- Selected tokens: {summary['selected_token_estimate']}",
            f"- Selection rate: {summary['selection_rate']:.2%}",
            f"- Mean duplicate content rate: {summary['mean_duplicate_content_rate']:.2%}",
        ]
        freshness = summary["mean_selected_freshness_generation_delta"]
        if freshness is not None:
            lines.append(f"- Mean selected freshness delta: {freshness:.2f} generation(s)")
        return "\n".join(lines)


def build_context_selection_report(
    decisions: Sequence[ContextSelectionDecision],
) -> ContextSelectionReport:
    stages = tuple(
        ContextSelectionStageSummary.from_decision(decision)
        for decision in sorted(decisions, key=lambda item: (item.generation, item.stage))
    )
    run_ids = {stage.run_id for stage in stages if stage.run_id}
    scenario_names = {stage.scenario_name for stage in stages if stage.scenario_name}
    if len(run_ids) > 1:
        raise ValueError("context selection report requires a single run_id")
    if len(scenario_names) > 1:
        raise ValueError("context selection report requires a single scenario_name")
    return ContextSelectionReport(
        run_id=next(iter(run_ids), ""),
        scenario_name=next(iter(scenario_names), ""),
        stages=stages,
    )


def _int_metric(metrics: dict[str, Any], key: str) -> int:
    try:
        return int(metrics.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _float_metric(metrics: dict[str, Any], key: str) -> float:
    try:
        return float(metrics.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _optional_float_metric(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _mean_optional(values: Iterable[float | None]) -> float | None:
    items = [value for value in values if value is not None]
    return sum(items) / len(items) if items else None
