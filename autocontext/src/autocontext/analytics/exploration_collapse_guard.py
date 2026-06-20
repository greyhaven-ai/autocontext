"""Advisory guard for guidance-induced exploration collapse."""

from __future__ import annotations

import json
from collections import Counter
from math import isfinite
from pathlib import Path
from statistics import mean
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field  # type: ignore[import-not-found]

GuidanceKind = Literal["hint", "playbook_update", "teacher_signal", "pressure_mode", "other"]
CollapseMetric = Literal[
    "response_length",
    "diversity",
    "entropy",
    "route_repetition",
    "rollback_rate",
    "score",
]
MitigationAction = Literal["none", "demote_guidance"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)


class ExplorationSnapshot(_StrictModel):
    generation_index: int = Field(ge=0)
    response_length: float = Field(ge=0)
    diversity: float | None = Field(default=None, ge=0.0)
    entropy: float | None = Field(default=None, ge=0.0)
    route_signature: str | None = None
    rollback_rate: float | None = Field(default=None, ge=0.0)
    score: float | None = None


class GuidanceChange(_StrictModel):
    change_id: str = Field(min_length=1)
    generation_index: int = Field(ge=0)
    kind: GuidanceKind
    source_component: str = Field(min_length=1)
    source_span: str | None = None


class ExplorationCollapseThresholds(_StrictModel):
    window: int = Field(default=2, ge=1)
    min_signals: int = Field(default=2, ge=1)
    response_length_drop_ratio: float = Field(default=0.25, ge=0.0, le=1.0)
    diversity_drop_ratio: float = Field(default=0.25, ge=0.0, le=1.0)
    entropy_drop_ratio: float = Field(default=0.25, ge=0.0, le=1.0)
    route_repetition_increase: float = Field(default=0.3, ge=0.0, le=1.0)
    rollback_rate_increase: float = Field(default=0.2, ge=0.0, le=1.0)
    score_drop: float = Field(default=0.05, ge=0.0)


class ExplorationCollapseSignal(_StrictModel):
    metric: CollapseMetric
    before: float
    after: float
    delta: float
    threshold: float


class ExplorationCollapseEvent(_StrictModel):
    event_type: Literal["exploration_collapse_detected"] = "exploration_collapse_detected"
    guidance_change: GuidanceChange
    advisory_only: bool
    mitigation: MitigationAction
    signals: list[ExplorationCollapseSignal]
    recommendation: str

    def to_record(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": {
                "guidance_change": self.guidance_change.to_dict(),
                "advisory_only": self.advisory_only,
                "mitigation": self.mitigation,
                "signals": [signal.to_dict() for signal in self.signals],
                "recommendation": self.recommendation,
            },
        }


class ExplorationCollapseReport(_StrictModel):
    schema_version: Literal[1] = 1
    advisory_only: bool
    events: list[ExplorationCollapseEvent]
    records: list[dict[str, Any]]


def detect_exploration_collapse(
    snapshots: list[ExplorationSnapshot],
    guidance_changes: list[GuidanceChange],
    *,
    advisory_only: bool = True,
    auto_mitigation: bool = False,
    thresholds: ExplorationCollapseThresholds | None = None,
) -> ExplorationCollapseReport:
    policy = thresholds or ExplorationCollapseThresholds()
    ordered = sorted(snapshots, key=lambda item: item.generation_index)
    events: list[ExplorationCollapseEvent] = []
    for change in guidance_changes:
        before = [item for item in ordered if item.generation_index < change.generation_index][-policy.window :]
        after = [item for item in ordered if item.generation_index >= change.generation_index][: policy.window]
        if not before or not after:
            continue
        signals = _signals(before, after, policy)
        if len(signals) >= policy.min_signals:
            events.append(
                ExplorationCollapseEvent(
                    guidance_change=change,
                    advisory_only=advisory_only,
                    mitigation="demote_guidance" if auto_mitigation and not advisory_only else "none",
                    signals=signals,
                    recommendation=_recommendation(auto_mitigation, advisory_only),
                )
            )
    return ExplorationCollapseReport(
        advisory_only=advisory_only,
        events=events,
        records=[event.to_record() for event in events],
    )


def persist_exploration_collapse_report(path: Path, report: ExplorationCollapseReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": report.schema_version,
        "advisory_only": report.advisory_only,
        "events": report.records,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_exploration_collapse_report(report: ExplorationCollapseReport) -> str:
    if not report.events:
        return "No exploration collapse detected."
    lines = ["# Exploration Collapse Guard", ""]
    for event in report.events:
        change = event.guidance_change
        metrics = ", ".join(signal.metric for signal in event.signals)
        span = f" span={change.source_span}" if change.source_span else ""
        lines.append(
            f"- {change.change_id} ({change.kind}) at generation {change.generation_index}: "
            f"source={change.source_component}{span}; metrics={metrics}; mitigation={event.mitigation}."
        )
    return "\n".join(lines) + "\n"


def _signals(
    before: list[ExplorationSnapshot],
    after: list[ExplorationSnapshot],
    thresholds: ExplorationCollapseThresholds,
) -> list[ExplorationCollapseSignal]:
    signals: list[ExplorationCollapseSignal] = []
    _drop_signal(
        signals,
        "response_length",
        _avg(before, "response_length"),
        _avg(after, "response_length"),
        thresholds.response_length_drop_ratio,
    )
    _drop_signal(signals, "diversity", _avg(before, "diversity"), _avg(after, "diversity"), thresholds.diversity_drop_ratio)
    _drop_signal(signals, "entropy", _avg(before, "entropy"), _avg(after, "entropy"), thresholds.entropy_drop_ratio)
    _rise_signal(
        signals,
        "route_repetition",
        _route_repetition(before),
        _route_repetition(after),
        thresholds.route_repetition_increase,
    )
    _rise_signal(
        signals,
        "rollback_rate",
        _avg(before, "rollback_rate"),
        _avg(after, "rollback_rate"),
        thresholds.rollback_rate_increase,
    )
    _absolute_drop_signal(signals, "score", _avg(before, "score"), _avg(after, "score"), thresholds.score_drop)
    return signals


def _drop_signal(
    signals: list[ExplorationCollapseSignal],
    metric: CollapseMetric,
    before: float | None,
    after: float | None,
    threshold: float,
) -> None:
    if before is None or after is None or before <= 0:
        return
    ratio = (before - after) / before
    if ratio >= threshold:
        _append_signal(signals, metric, before, after, threshold)


def _absolute_drop_signal(
    signals: list[ExplorationCollapseSignal],
    metric: CollapseMetric,
    before: float | None,
    after: float | None,
    threshold: float,
) -> None:
    if before is not None and after is not None and before - after >= threshold:
        _append_signal(signals, metric, before, after, threshold)


def _rise_signal(
    signals: list[ExplorationCollapseSignal],
    metric: CollapseMetric,
    before: float | None,
    after: float | None,
    threshold: float,
) -> None:
    if before is not None and after is not None and after - before >= threshold:
        _append_signal(signals, metric, before, after, threshold)


def _append_signal(
    signals: list[ExplorationCollapseSignal],
    metric: CollapseMetric,
    before: float,
    after: float,
    threshold: float,
) -> None:
    signals.append(
        ExplorationCollapseSignal(
            metric=metric,
            before=before,
            after=after,
            delta=after - before,
            threshold=threshold,
        )
    )


def _avg(items: list[ExplorationSnapshot], attr: str) -> float | None:
    values = [float(value) for item in items if (value := getattr(item, attr)) is not None and isfinite(float(value))]
    return round(mean(values), 6) if values else None


def _route_repetition(items: list[ExplorationSnapshot]) -> float | None:
    routes = [item.route_signature for item in items if item.route_signature]
    if not routes:
        return None
    return round(Counter(routes).most_common(1)[0][1] / len(routes), 6)


def _recommendation(auto_mitigation: bool, advisory_only: bool) -> str:
    if auto_mitigation and not advisory_only:
        return "Demote the associated guidance and switch to exploration-heavy sampling."
    return "Warn only; inspect the associated guidance before changing run behavior."
