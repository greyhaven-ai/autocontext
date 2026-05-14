from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from autocontext.knowledge.context_selection import ContextSelectionDecision


@dataclass(frozen=True)
class ContextSelectionDiagnosticPolicy:
    duplicate_content_rate_threshold: float = 0.25
    useful_artifact_recall_floor: float = 0.70
    selected_token_estimate_threshold: int = 8000
    compaction_cache_hit_rate_floor: float = 0.50
    compaction_cache_min_lookups: int = 5


@dataclass(frozen=True)
class ContextSelectionDiagnostic:
    code: str
    severity: str
    metric_name: str
    value: float
    threshold: float
    message: str
    recommendation: str
    generation: int
    stage: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "metric_name": self.metric_name,
            "value": self.value,
            "threshold": self.threshold,
            "message": self.message,
            "recommendation": self.recommendation,
            "generation": self.generation,
            "stage": self.stage,
        }


@dataclass(frozen=True)
class ContextSelectionTelemetryCard:
    """Operator-facing summary tile for context-selection observability."""

    key: str
    label: str
    value: str
    severity: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "severity": self.severity,
            "detail": self.detail,
        }


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
    budget_input_token_estimate: int
    budget_output_token_estimate: int
    budget_token_reduction: int
    budget_dedupe_hit_count: int
    budget_component_cap_hit_count: int
    budget_trimmed_component_count: int
    compaction_cache_hits: int
    compaction_cache_misses: int
    compaction_cache_lookups: int
    compaction_cache_hit_rate: float | None

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
            budget_input_token_estimate=_int_metric(metrics, "budget_input_token_estimate"),
            budget_output_token_estimate=_int_metric(metrics, "budget_output_token_estimate"),
            budget_token_reduction=_int_metric(metrics, "budget_token_reduction"),
            budget_dedupe_hit_count=_int_metric(metrics, "budget_dedupe_hit_count"),
            budget_component_cap_hit_count=_int_metric(metrics, "budget_component_cap_hit_count"),
            budget_trimmed_component_count=_int_metric(metrics, "budget_trimmed_component_count"),
            compaction_cache_hits=_int_metric(metrics, "compaction_cache_hits"),
            compaction_cache_misses=_int_metric(metrics, "compaction_cache_misses"),
            compaction_cache_lookups=_int_metric(metrics, "compaction_cache_lookups"),
            compaction_cache_hit_rate=_optional_float_metric(metrics, "compaction_cache_hit_rate"),
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
            "budget_input_token_estimate": self.budget_input_token_estimate,
            "budget_output_token_estimate": self.budget_output_token_estimate,
            "budget_token_reduction": self.budget_token_reduction,
            "budget_dedupe_hit_count": self.budget_dedupe_hit_count,
            "budget_component_cap_hit_count": self.budget_component_cap_hit_count,
            "budget_trimmed_component_count": self.budget_trimmed_component_count,
            "compaction_cache_hits": self.compaction_cache_hits,
            "compaction_cache_misses": self.compaction_cache_misses,
            "compaction_cache_lookups": self.compaction_cache_lookups,
            "compaction_cache_hit_rate": self.compaction_cache_hit_rate,
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
        compaction_cache_hits = sum(stage.compaction_cache_hits for stage in self.stages)
        compaction_cache_lookups = sum(stage.compaction_cache_lookups for stage in self.stages)
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
            "budget_input_token_estimate": sum(stage.budget_input_token_estimate for stage in self.stages),
            "budget_output_token_estimate": sum(stage.budget_output_token_estimate for stage in self.stages),
            "budget_token_reduction": sum(stage.budget_token_reduction for stage in self.stages),
            "budget_dedupe_hit_count": sum(stage.budget_dedupe_hit_count for stage in self.stages),
            "budget_component_cap_hit_count": sum(stage.budget_component_cap_hit_count for stage in self.stages),
            "budget_trimmed_component_count": sum(stage.budget_trimmed_component_count for stage in self.stages),
            "compaction_cache_hits": compaction_cache_hits,
            "compaction_cache_misses": sum(stage.compaction_cache_misses for stage in self.stages),
            "compaction_cache_lookups": compaction_cache_lookups,
            "compaction_cache_hit_rate": (
                compaction_cache_hits / compaction_cache_lookups
                if compaction_cache_lookups
                else None
            ),
        }

    def diagnostics(
        self,
        policy: ContextSelectionDiagnosticPolicy | None = None,
    ) -> tuple[ContextSelectionDiagnostic, ...]:
        policy = policy or ContextSelectionDiagnosticPolicy()
        if not self.stages:
            return ()

        diagnostics: list[ContextSelectionDiagnostic] = []
        duplicate_stage = max(self.stages, key=lambda stage: stage.duplicate_content_rate)
        if duplicate_stage.duplicate_content_rate >= policy.duplicate_content_rate_threshold:
            diagnostics.append(
                ContextSelectionDiagnostic(
                    code="HIGH_DUPLICATE_CONTENT_RATE",
                    severity="warning",
                    metric_name="duplicate_content_rate",
                    value=duplicate_stage.duplicate_content_rate,
                    threshold=policy.duplicate_content_rate_threshold,
                    message="Selected context contains repeated content in a single prompt assembly stage.",
                    recommendation=(
                        "Deduplicate equivalent prompt components before selection and keep one canonical source."
                    ),
                    generation=duplicate_stage.generation,
                    stage=duplicate_stage.stage,
                )
            )

        useful_stages = [stage for stage in self.stages if stage.useful_artifact_recall is not None]
        if useful_stages:
            recall_stage = min(useful_stages, key=lambda stage: stage.useful_artifact_recall or 0.0)
            recall = recall_stage.useful_artifact_recall
            if recall is not None and recall < policy.useful_artifact_recall_floor:
                diagnostics.append(
                    ContextSelectionDiagnostic(
                        code="LOW_USEFUL_ARTIFACT_RECALL",
                        severity="warning",
                        metric_name="useful_artifact_recall",
                        value=recall,
                        threshold=policy.useful_artifact_recall_floor,
                        message="Useful artifacts were available but omitted from selected context.",
                        recommendation=(
                            "Promote useful artifacts earlier in context ranking or lower-priority noisy components."
                        ),
                        generation=recall_stage.generation,
                        stage=recall_stage.stage,
                    )
                )

        token_stage = max(self.stages, key=lambda stage: stage.selected_token_estimate)
        if token_stage.selected_token_estimate > policy.selected_token_estimate_threshold:
            diagnostics.append(
                ContextSelectionDiagnostic(
                    code="SELECTED_TOKEN_BLOAT",
                    severity="warning",
                    metric_name="selected_token_estimate",
                    value=float(token_stage.selected_token_estimate),
                    threshold=float(policy.selected_token_estimate_threshold),
                    message="One prompt assembly stage selected an unusually large context payload.",
                    recommendation="Reduce selected context by tightening budget filters and summarizing bulky artifacts.",
                    generation=token_stage.generation,
                    stage=token_stage.stage,
                )
            )
        cache_stages = [
            stage
            for stage in self.stages
            if stage.compaction_cache_hit_rate is not None
            and stage.compaction_cache_lookups >= policy.compaction_cache_min_lookups
        ]
        if cache_stages:
            cache_stage = min(cache_stages, key=lambda stage: stage.compaction_cache_hit_rate or 0.0)
            hit_rate = cache_stage.compaction_cache_hit_rate
            if hit_rate is not None and hit_rate < policy.compaction_cache_hit_rate_floor:
                diagnostics.append(
                    ContextSelectionDiagnostic(
                        code="LOW_COMPACTION_CACHE_HIT_RATE",
                        severity="info",
                        metric_name="compaction_cache_hit_rate",
                        value=hit_rate,
                        threshold=policy.compaction_cache_hit_rate_floor,
                        message="Semantic compaction cache reuse was low for a prompt assembly stage.",
                        recommendation=(
                            "Check whether repeated prompt components use stable canonical text before cache lookup."
                        ),
                        generation=cache_stage.generation,
                        stage=cache_stage.stage,
                    )
                )
        return tuple(diagnostics)

    def telemetry_cards(
        self,
        policy: ContextSelectionDiagnosticPolicy | None = None,
    ) -> tuple[ContextSelectionTelemetryCard, ...]:
        summary = self.summary()
        diagnostics = self.diagnostics(policy)
        diagnostic_codes = {diagnostic.code for diagnostic in diagnostics}
        return (
            _selected_context_card(summary, diagnostic_codes),
            _context_budget_card(summary),
            _semantic_compaction_cache_card(summary, diagnostic_codes),
            _diagnostics_card(diagnostics),
        )

    def to_dict(self) -> dict[str, Any]:
        generations = {stage.generation for stage in self.stages}
        diagnostics = self.diagnostics()
        return {
            "status": "completed",
            "run_id": self.run_id,
            "scenario_name": self.scenario_name,
            "decision_count": len(self.stages),
            "generation_count": len(generations),
            "summary": self.summary(),
            "telemetry_cards": [card.to_dict() for card in self.telemetry_cards()],
            "diagnostic_count": len(diagnostics),
            "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
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
        lines.extend(
            [
                "",
                "## Context Budget",
                f"- Input estimate: {summary['budget_input_token_estimate']}",
                f"- Output estimate: {summary['budget_output_token_estimate']}",
                f"- Token reduction: {summary['budget_token_reduction']}",
                f"- Dedupe hits: {summary['budget_dedupe_hit_count']}",
                f"- Component caps: {summary['budget_component_cap_hit_count']}",
                f"- Global trims: {summary['budget_trimmed_component_count']}",
                "",
                "## Semantic Compaction Cache",
                f"- Hit rate: {_format_optional_percent(summary['compaction_cache_hit_rate'])}",
                f"- Hits: {summary['compaction_cache_hits']}",
                f"- Misses: {summary['compaction_cache_misses']}",
                f"- Lookups: {summary['compaction_cache_lookups']}",
            ]
        )
        diagnostics = self.diagnostics()
        if diagnostics:
            lines.extend(["", "## Diagnostics"])
            for diagnostic in diagnostics:
                lines.append(f"- {diagnostic.code}: {diagnostic.recommendation}")
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


def _selected_context_card(
    summary: dict[str, Any],
    diagnostic_codes: set[str],
) -> ContextSelectionTelemetryCard:
    severity = "warning" if "SELECTED_TOKEN_BLOAT" in diagnostic_codes else "ok"
    return ContextSelectionTelemetryCard(
        key="selected_context",
        label="Selected context",
        value=f"{_int_metric(summary, 'selected_token_estimate')} est. tokens",
        severity=severity,
        detail=(
            f"{_int_metric(summary, 'selected_count')}/{_int_metric(summary, 'candidate_count')} components "
            f"selected ({_float_metric(summary, 'selection_rate'):.1%})"
        ),
    )


def _context_budget_card(summary: dict[str, Any]) -> ContextSelectionTelemetryCard:
    input_tokens = _int_metric(summary, "budget_input_token_estimate")
    output_tokens = _int_metric(summary, "budget_output_token_estimate")
    token_reduction = _int_metric(summary, "budget_token_reduction")
    dedupe_hits = _int_metric(summary, "budget_dedupe_hit_count")
    cap_hits = _int_metric(summary, "budget_component_cap_hit_count")
    trim_hits = _int_metric(summary, "budget_trimmed_component_count")
    if input_tokens <= 0:
        return ContextSelectionTelemetryCard(
            key="context_budget",
            label="Context budget",
            value="No telemetry",
            severity="info",
            detail="No context budget telemetry recorded.",
        )
    severity = "warning" if trim_hits > 0 else "ok"
    return ContextSelectionTelemetryCard(
        key="context_budget",
        label="Context budget",
        value=f"{token_reduction} est. tokens reduced",
        severity=severity,
        detail=(
            f"{input_tokens}->{output_tokens} est. tokens; "
            f"{dedupe_hits} dedupe, {cap_hits} caps, {trim_hits} trims"
        ),
    )


def _semantic_compaction_cache_card(
    summary: dict[str, Any],
    diagnostic_codes: set[str],
) -> ContextSelectionTelemetryCard:
    lookups = _int_metric(summary, "compaction_cache_lookups")
    hit_rate = _optional_float_metric(summary, "compaction_cache_hit_rate")
    if lookups <= 0 or hit_rate is None:
        return ContextSelectionTelemetryCard(
            key="semantic_compaction_cache",
            label="Semantic compaction cache",
            value="No lookups",
            severity="info",
            detail="No semantic compaction cache lookups recorded.",
        )
    severity = "warning" if "LOW_COMPACTION_CACHE_HIT_RATE" in diagnostic_codes else "ok"
    return ContextSelectionTelemetryCard(
        key="semantic_compaction_cache",
        label="Semantic compaction cache",
        value=f"{hit_rate:.1%} hit rate",
        severity=severity,
        detail=(
            f"{_int_metric(summary, 'compaction_cache_hits')} hits, "
            f"{_int_metric(summary, 'compaction_cache_misses')} misses, {lookups} lookups"
        ),
    )


def _diagnostics_card(diagnostics: tuple[ContextSelectionDiagnostic, ...]) -> ContextSelectionTelemetryCard:
    severity = "warning" if diagnostics else "ok"
    detail = ", ".join(diagnostic.code for diagnostic in diagnostics) if diagnostics else "No diagnostics."
    return ContextSelectionTelemetryCard(
        key="diagnostics",
        label="Diagnostics",
        value=f"{len(diagnostics)} finding(s)",
        severity=severity,
        detail=detail,
    )


def _format_optional_percent(value: Any) -> str:
    try:
        return f"{float(value):.1%}" if value is not None else "n/a"
    except (TypeError, ValueError):
        return "n/a"
