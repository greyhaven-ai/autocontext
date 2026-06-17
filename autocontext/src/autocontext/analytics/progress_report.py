"""Run progress curves, milestones, pass@k, and branch lineage reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

ProgressMilestoneName = Literal[
    "first_valid_candidate",
    "first_passing_verifier",
    "first_advancement",
    "threshold_success",
]

MILESTONE_NAMES: tuple[ProgressMilestoneName, ...] = (
    "first_valid_candidate",
    "first_passing_verifier",
    "first_advancement",
    "threshold_success",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)


class ProgressPoint(_StrictModel):
    """One scored candidate on the best-score-over-time curve."""

    event_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    elapsed_seconds: float
    generation_index: int | None = None
    hypothesis_node_id: str | None = None
    candidate_id: str | None = None
    score: float
    best_score: float
    improved: bool
    milestone_names: list[ProgressMilestoneName] = Field(default_factory=list)


class MilestoneTiming(_StrictModel):
    """Time to a named operator-facing progress milestone."""

    name: ProgressMilestoneName
    reached: bool
    event_id: str | None = None
    timestamp: str | None = None
    elapsed_seconds: float | None = None
    generation_index: int | None = None
    hypothesis_node_id: str | None = None
    score: float | None = None


class PassAtKSummary(_StrictModel):
    """Observed pass@k / best-of-k summary for the first k candidates."""

    k: int
    trials_considered: int
    successes: int
    passed: bool
    best_score: float | None
    threshold: float


class BranchLineageEdge(_StrictModel):
    """Parent/child hypothesis edge included in the inspection artifact."""

    parent_hypothesis_node_id: str = Field(min_length=1)
    child_hypothesis_node_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    generation_index: int | None = None


class RunProgressReport(_StrictModel):
    """Durable progress report shared by Python and TypeScript."""

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    threshold: float
    progress_points: list[ProgressPoint]
    milestones: list[MilestoneTiming]
    pass_at_k: list[PassAtKSummary]
    branch_lineage: list[BranchLineageEdge]

    def to_markdown(self) -> str:
        best_score = max((point.best_score for point in self.progress_points), default=None)
        pass_lines = [
            f"- pass@{summary.k}: {'pass' if summary.passed else 'miss'} "
            f"({summary.successes}/{summary.trials_considered}, best={summary.best_score})"
            for summary in self.pass_at_k
        ]
        milestone_lines = [
            f"- {milestone.name}: {milestone.elapsed_seconds:.3f}s"
            for milestone in self.milestones
            if milestone.reached and milestone.elapsed_seconds is not None
        ]
        parts = [
            f"# Progress Report: {self.run_id}",
            f"- Best score: {best_score}",
            f"- Threshold: {self.threshold}",
            "",
            "## Milestones",
            *(milestone_lines or ["- None reached"]),
            "",
            "## Pass@k",
            *(pass_lines or ["- No trials"]),
            "",
        ]
        return "\n".join(parts)


def build_run_progress_report(
    *,
    run_id: str,
    events: list[dict[str, Any]],
    threshold: float,
    pass_at_k_values: list[int] | None = None,
    generated_at: str | None = None,
) -> RunProgressReport:
    """Build a progress report from tree-search or campaign events."""

    ordered = sorted((_normalize_event(event) for event in events), key=_event_sort_key)
    start = _start_time(ordered, generated_at)
    scored = [event for event in ordered if _score(event) is not None]
    milestones_by_name = _milestones(ordered, scored, threshold, start)

    return RunProgressReport(
        run_id=run_id,
        generated_at=generated_at or datetime.now().astimezone().isoformat(),
        threshold=threshold,
        progress_points=_progress_points(scored, milestones_by_name, start),
        milestones=[milestones_by_name[name] for name in MILESTONE_NAMES],
        pass_at_k=_pass_at_k(scored, threshold, pass_at_k_values if pass_at_k_values is not None else [1, 5, 10]),
        branch_lineage=_branch_lineage(ordered),
    )


def progress_report_reference(report: RunProgressReport) -> dict[str, Any]:
    """Small inspection-safe pointer to a full progress report."""

    best_score = max((point.best_score for point in report.progress_points), default=None)
    return {
        "run_id": report.run_id,
        "threshold": report.threshold,
        "best_score": best_score,
        "milestones_reached": sum(1 for milestone in report.milestones if milestone.reached),
        "pass_at_k": [summary.model_dump(mode="json") for summary in report.pass_at_k],
    }


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return event
    event_id = event.get("event_id") or (f"seq-{event.get('seq')}" if event.get("seq") is not None else "")
    return {
        "event_id": event_id,
        "event_type": event.get("event"),
        "timestamp": event.get("ts") or event.get("timestamp"),
        "generation_index": payload.get("generation_index") or payload.get("generation"),
        "hypothesis_node_id": payload.get("hypothesis_node_id") or payload.get("node_id") or payload.get("child_id"),
        "parent_hypothesis_node_id": payload.get("parent_hypothesis_node_id") or payload.get("parent_id"),
        "candidate_id": payload.get("candidate_id") or payload.get("match_index"),
        "score": payload.get("score") if payload.get("score") is not None else payload.get("best_score"),
        "verifier_passed": payload.get("verifier_passed"),
        "gate_decision": payload.get("gate_decision") or payload.get("decision"),
    }


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str]:
    return (_string(event.get("timestamp")), _string(event.get("event_id")))


def _start_time(events: list[dict[str, Any]], generated_at: str | None) -> datetime:
    timestamps = [_parse_time(_string(event.get("timestamp"))) for event in events if event.get("timestamp")]
    if timestamps:
        return min(timestamps)
    if generated_at:
        return _parse_time(generated_at)
    return datetime.now().astimezone()


def _milestones(
    events: list[dict[str, Any]],
    scored: list[dict[str, Any]],
    threshold: float,
    start: datetime,
) -> dict[ProgressMilestoneName, MilestoneTiming]:
    return {
        "first_valid_candidate": _milestone("first_valid_candidate", _first(scored), start),
        "first_passing_verifier": _milestone(
            "first_passing_verifier",
            _first(event for event in scored if event.get("verifier_passed") is True),
            start,
        ),
        "first_advancement": _milestone(
            "first_advancement",
            _first(event for event in events if _string(event.get("gate_decision") or event.get("decision")) == "advance"),
            start,
        ),
        "threshold_success": _milestone(
            "threshold_success",
            _first(event for event in scored if (_score(event) or 0.0) >= threshold),
            start,
        ),
    }


def _milestone(name: ProgressMilestoneName, event: dict[str, Any] | None, start: datetime) -> MilestoneTiming:
    if event is None:
        return MilestoneTiming(name=name, reached=False)
    timestamp = _string(event.get("timestamp"))
    return MilestoneTiming(
        name=name,
        reached=True,
        event_id=_string(event.get("event_id")),
        timestamp=timestamp,
        elapsed_seconds=_elapsed_seconds(timestamp, start),
        generation_index=_int_or_none(event.get("generation_index")),
        hypothesis_node_id=_str_or_none(event.get("hypothesis_node_id")),
        score=_score(event),
    )


def _progress_points(
    scored: list[dict[str, Any]],
    milestones_by_name: dict[ProgressMilestoneName, MilestoneTiming],
    start: datetime,
) -> list[ProgressPoint]:
    point_milestones: dict[str, list[ProgressMilestoneName]] = {}
    for milestone in milestones_by_name.values():
        if milestone.event_id:
            point_milestones.setdefault(milestone.event_id, []).append(milestone.name)

    points: list[ProgressPoint] = []
    best = float("-inf")
    for event in scored:
        score = _score(event)
        if score is None:
            continue
        improved = score > best
        if improved:
            best = score
        timestamp = _string(event.get("timestamp"))
        points.append(
            ProgressPoint(
                event_id=_string(event.get("event_id")),
                timestamp=timestamp,
                elapsed_seconds=_elapsed_seconds(timestamp, start),
                generation_index=_int_or_none(event.get("generation_index")),
                hypothesis_node_id=_str_or_none(event.get("hypothesis_node_id")),
                candidate_id=_str_or_none(event.get("candidate_id")),
                score=score,
                best_score=best,
                improved=improved,
                milestone_names=point_milestones.get(_string(event.get("event_id")), []),
            )
        )
    return points


def _pass_at_k(scored: list[dict[str, Any]], threshold: float, values: list[int]) -> list[PassAtKSummary]:
    scores = [_score(event) for event in scored]
    numeric_scores = [score for score in scores if score is not None]
    summaries: list[PassAtKSummary] = []
    for k in values:
        if k <= 0:
            continue
        window = numeric_scores[:k]
        successes = sum(1 for score in window if score >= threshold)
        summaries.append(
            PassAtKSummary(
                k=k,
                trials_considered=len(window),
                successes=successes,
                passed=successes > 0,
                best_score=max(window) if window else None,
                threshold=threshold,
            )
        )
    return summaries


def _branch_lineage(events: list[dict[str, Any]]) -> list[BranchLineageEdge]:
    seen: set[tuple[str, str]] = set()
    edges: list[BranchLineageEdge] = []
    for event in events:
        parent = _str_or_none(event.get("parent_hypothesis_node_id"))
        child = _str_or_none(event.get("hypothesis_node_id"))
        if not parent or not child or parent == child or (parent, child) in seen:
            continue
        seen.add((parent, child))
        edges.append(
            BranchLineageEdge(
                parent_hypothesis_node_id=parent,
                child_hypothesis_node_id=child,
                event_id=_string(event.get("event_id")),
                generation_index=_int_or_none(event.get("generation_index")),
            )
        )
    return edges


def _first(events: Any) -> dict[str, Any] | None:
    return next(iter(events), None)


def _score(event: dict[str, Any]) -> float | None:
    value = event.get("score")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _elapsed_seconds(timestamp: str, start: datetime) -> float:
    return (_parse_time(timestamp) - start).total_seconds()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "BranchLineageEdge",
    "MILESTONE_NAMES",
    "MilestoneTiming",
    "PassAtKSummary",
    "ProgressMilestoneName",
    "ProgressPoint",
    "RunProgressReport",
    "build_run_progress_report",
    "progress_report_reference",
]
