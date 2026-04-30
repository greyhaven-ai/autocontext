"""Rubric-drift monitoring across runs and releases (AC-259).

Detects when judge rubric or scoring behavior is drifting toward
surface-style overfit, unstable dimensions, or unreliable scoring.
Tracks dimension stability, score inflation/compression, revision-to-perfect
jumps, and emits structured warnings when thresholds are crossed.
"""

from __future__ import annotations

import json
import statistics
import uuid
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autocontext.analytics.facets import RunFacet
from autocontext.util.json_io import read_json, write_json

# Score at or above this is considered "near-perfect"
_PERFECT_THRESHOLD = 0.95


class RubricSnapshot(BaseModel):
    """Point-in-time rubric-level metrics for a window of runs."""

    snapshot_id: str
    created_at: str
    window_start: str
    window_end: str
    run_count: int
    mean_score: float
    median_score: float
    stddev_score: float
    min_score: float
    max_score: float
    score_inflation_rate: float
    perfect_score_rate: float
    revision_jump_rate: float
    retry_rate: float
    rollback_rate: float
    release: str
    scenario_family: str
    agent_provider: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RubricSnapshot:
        return cls.model_validate(data)


class DimensionDriftSnapshot(BaseModel):
    """Point-in-time dimension-level score trajectories for one run."""

    snapshot_id: str
    created_at: str
    run_id: str
    generation_count: int
    dimension_count: int
    dimension_series: dict[str, list[float]]
    best_dimension_series: dict[str, list[float]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DimensionDriftSnapshot:
        return cls.model_validate(data)


class DriftThresholds(BaseModel):
    """Configurable thresholds for drift detection."""

    max_score_inflation: float = 0.15
    max_perfect_rate: float = 0.5
    max_revision_jump_rate: float = 0.4
    min_stddev: float = 0.05
    max_retry_rate: float = 0.5
    max_rollback_rate: float = 0.3
    min_dimension_observations: int = 3
    min_dimension_stddev: float = 0.01
    max_dimension_decline: float = 0.04
    max_dimension_correlation: float = 0.98
    min_within_gen_variance_zero_streak: int = 3


class DriftWarning(BaseModel):
    """A structured warning when rubric drift is detected."""

    warning_id: str
    created_at: str
    warning_type: str
    severity: str
    description: str
    snapshot_id: str
    metric_name: str
    metric_value: float
    threshold_value: float
    affected_scenarios: list[str]
    affected_providers: list[str]
    affected_releases: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DriftWarning:
        return cls.model_validate(data)


class RubricDriftMonitor:
    """Monitors rubric-level metrics for drift across runs."""

    def __init__(self, thresholds: DriftThresholds | None = None) -> None:
        self._thresholds = thresholds or DriftThresholds()

    def compute_snapshot(
        self,
        facets: list[RunFacet],
        release: str = "",
        scenario_family: str = "",
        agent_provider: str = "",
    ) -> RubricSnapshot:
        now = datetime.now(UTC).isoformat()
        scenarios = sorted({facet.scenario for facet in facets if facet.scenario})

        if not facets:
            return RubricSnapshot(
                snapshot_id=f"snap-{uuid.uuid4().hex[:8]}",
                created_at=now,
                window_start="",
                window_end="",
                run_count=0,
                mean_score=0.0,
                median_score=0.0,
                stddev_score=0.0,
                min_score=0.0,
                max_score=0.0,
                score_inflation_rate=0.0,
                perfect_score_rate=0.0,
                revision_jump_rate=0.0,
                retry_rate=0.0,
                rollback_rate=0.0,
                release=release,
                scenario_family=scenario_family,
                agent_provider=agent_provider,
                metadata={"scenarios": scenarios},
            )

        scores = [f.best_score for f in facets]
        timestamps = sorted(f.created_at for f in facets if f.created_at)
        window_start = timestamps[0] if timestamps else ""
        window_end = timestamps[-1] if timestamps else ""

        mean_score = statistics.mean(scores)
        median_score = statistics.median(scores)
        stddev_score = statistics.pstdev(scores) if len(scores) > 1 else 0.0

        # Perfect score rate
        perfect_count = sum(1 for s in scores if s >= _PERFECT_THRESHOLD)
        perfect_score_rate = perfect_count / len(facets)

        # Revision jump rate: strong_improvement signals / total_generations
        total_gens = sum(f.total_generations for f in facets)
        strong_improvements = sum(
            1 for f in facets
            for d in f.delight_signals
            if d.signal_type == "strong_improvement"
        )
        revision_jump_rate = strong_improvements / total_gens if total_gens > 0 else 0.0

        # Retry/rollback rates
        total_retries = sum(f.retries for f in facets)
        total_rollbacks = sum(f.rollbacks for f in facets)
        retry_rate = total_retries / total_gens if total_gens > 0 else 0.0
        rollback_rate = total_rollbacks / total_gens if total_gens > 0 else 0.0

        # Score inflation: compare first-half mean to second-half mean
        sorted_facets = sorted(facets, key=lambda f: f.created_at or "")
        mid = len(sorted_facets) // 2
        if mid > 0:
            first_half_mean = statistics.mean(f.best_score for f in sorted_facets[:mid])
            second_half_mean = statistics.mean(f.best_score for f in sorted_facets[mid:])
            score_inflation_rate = second_half_mean - first_half_mean
        else:
            score_inflation_rate = 0.0

        return RubricSnapshot(
            snapshot_id=f"snap-{uuid.uuid4().hex[:8]}",
            created_at=now,
            window_start=window_start,
            window_end=window_end,
            run_count=len(facets),
            mean_score=round(mean_score, 4),
            median_score=round(median_score, 4),
            stddev_score=round(stddev_score, 4),
            min_score=min(scores),
            max_score=max(scores),
            score_inflation_rate=round(score_inflation_rate, 4),
            perfect_score_rate=round(perfect_score_rate, 4),
            revision_jump_rate=round(revision_jump_rate, 4),
            retry_rate=round(retry_rate, 4),
            rollback_rate=round(rollback_rate, 4),
            release=release,
            scenario_family=scenario_family,
            agent_provider=agent_provider,
            metadata={"scenarios": scenarios},
        )

    def compute_dimension_snapshot(
        self,
        run_id: str,
        generation_trajectory: list[dict[str, Any]],
    ) -> DimensionDriftSnapshot:
        """Build dimension score series from generation trajectory rows."""
        now = datetime.now(UTC).isoformat()
        dimension_series: dict[str, list[float]] = {}
        best_dimension_series: dict[str, list[float]] = {}
        generation_indexes: list[int] = []

        for row in generation_trajectory:
            summary = _dimension_summary_from_row(row)
            if not summary:
                continue
            generation = row.get("generation_index")
            if isinstance(generation, int):
                generation_indexes.append(generation)
            _append_dimension_values(dimension_series, summary.get("dimension_means"))
            _append_dimension_values(best_dimension_series, summary.get("best_dimensions"))

        return DimensionDriftSnapshot(
            snapshot_id=f"dim-snap-{uuid.uuid4().hex[:8]}",
            created_at=now,
            run_id=run_id,
            generation_count=len(generation_indexes) if generation_indexes else len(generation_trajectory),
            dimension_count=len(dimension_series),
            dimension_series=dimension_series,
            best_dimension_series=best_dimension_series,
            metadata={"generation_indexes": generation_indexes},
        )

    def detect_drift(
        self,
        current: RubricSnapshot,
        baseline: RubricSnapshot | None = None,
    ) -> list[DriftWarning]:
        if current.run_count == 0:
            return []

        thresholds = self._thresholds
        now = datetime.now(UTC).isoformat()
        warnings: list[DriftWarning] = []

        raw_scenarios = current.metadata.get("scenarios", [])
        if isinstance(raw_scenarios, list):
            scenarios = sorted({str(s) for s in raw_scenarios if s})
        else:
            scenario = current.metadata.get("scenario", "")
            scenarios = [scenario] if scenario else []
        providers = [current.agent_provider] if current.agent_provider else []
        releases = [current.release] if current.release else []

        # Score inflation — from snapshot internal trend
        if current.score_inflation_rate > thresholds.max_score_inflation:
            warnings.append(self._make_warning(
                now, "score_inflation", "high",
                f"Score inflation rate {current.score_inflation_rate:.2f} "
                f"exceeds threshold {thresholds.max_score_inflation:.2f}",
                current.snapshot_id,
                "score_inflation_rate", current.score_inflation_rate,
                thresholds.max_score_inflation,
                scenarios, providers, releases,
            ))

        # Score inflation — baseline comparison
        if baseline is not None:
            delta = current.mean_score - baseline.mean_score
            if delta > thresholds.max_score_inflation:
                warnings.append(self._make_warning(
                    now, "score_inflation", "high",
                    f"Mean score increased by {delta:.2f} from baseline "
                    f"({baseline.mean_score:.2f} → {current.mean_score:.2f})",
                    current.snapshot_id,
                    "mean_score_delta", delta,
                    thresholds.max_score_inflation,
                    scenarios, providers, releases,
                ))

        # Perfect rate
        if current.perfect_score_rate > thresholds.max_perfect_rate:
            warnings.append(self._make_warning(
                now, "perfect_rate_high", "high",
                f"Perfect score rate {current.perfect_score_rate:.0%} "
                f"exceeds threshold {thresholds.max_perfect_rate:.0%}",
                current.snapshot_id,
                "perfect_score_rate", current.perfect_score_rate,
                thresholds.max_perfect_rate,
                scenarios, providers, releases,
            ))

        # Score compression
        if current.stddev_score < thresholds.min_stddev and current.run_count > 1:
            warnings.append(self._make_warning(
                now, "score_compression", "medium",
                f"Score stddev {current.stddev_score:.4f} below "
                f"minimum {thresholds.min_stddev:.4f}",
                current.snapshot_id,
                "stddev_score", current.stddev_score,
                thresholds.min_stddev,
                scenarios, providers, releases,
            ))

        # Revision jump rate
        if current.revision_jump_rate > thresholds.max_revision_jump_rate:
            warnings.append(self._make_warning(
                now, "revision_jump_rate_high", "medium",
                f"Revision jump rate {current.revision_jump_rate:.0%} "
                f"exceeds threshold {thresholds.max_revision_jump_rate:.0%}",
                current.snapshot_id,
                "revision_jump_rate", current.revision_jump_rate,
                thresholds.max_revision_jump_rate,
                scenarios, providers, releases,
            ))

        # Retry rate
        if current.retry_rate > thresholds.max_retry_rate:
            warnings.append(self._make_warning(
                now, "retry_rate_high", "medium",
                f"Retry rate {current.retry_rate:.0%} "
                f"exceeds threshold {thresholds.max_retry_rate:.0%}",
                current.snapshot_id,
                "retry_rate", current.retry_rate,
                thresholds.max_retry_rate,
                scenarios, providers, releases,
            ))

        # Rollback rate
        if current.rollback_rate > thresholds.max_rollback_rate:
            warnings.append(self._make_warning(
                now, "rollback_rate_high", "high",
                f"Rollback rate {current.rollback_rate:.0%} "
                f"exceeds threshold {thresholds.max_rollback_rate:.0%}",
                current.snapshot_id,
                "rollback_rate", current.rollback_rate,
                thresholds.max_rollback_rate,
                scenarios, providers, releases,
            ))

        return warnings

    def detect_dimension_drift(
        self,
        current: DimensionDriftSnapshot,
        *,
        scenario: str = "",
        release: str = "",
        agent_provider: str = "",
    ) -> list[DriftWarning]:
        """Detect dimension-level scoring drift within a run trajectory."""
        if current.generation_count == 0 or current.dimension_count == 0:
            return []

        thresholds = self._thresholds
        now = datetime.now(UTC).isoformat()
        warnings: list[DriftWarning] = []
        scenarios = [scenario] if scenario else []
        providers = [agent_provider] if agent_provider else []
        releases = [release] if release else []

        for dimension, series in sorted(current.dimension_series.items()):
            if len(series) < thresholds.min_dimension_observations:
                continue
            stddev = statistics.pstdev(series) if len(series) > 1 else 0.0
            if stddev <= thresholds.min_dimension_stddev:
                warnings.append(self._make_warning(
                    now,
                    "dimension_score_compression",
                    "medium",
                    f"Dimension '{dimension}' score stddev {stddev:.4f} is at or below "
                    f"{thresholds.min_dimension_stddev:.4f}",
                    current.snapshot_id,
                    f"dimension.{dimension}.stddev",
                    stddev,
                    thresholds.min_dimension_stddev,
                    scenarios,
                    providers,
                    releases,
                    metadata={"run_id": current.run_id, "dimension": dimension, "series": series},
                ))

            best_series = current.best_dimension_series.get(dimension, [])
            zero_variance_streak = _max_equal_streak(
                series,
                best_series,
                limit=thresholds.min_within_gen_variance_zero_streak,
            )
            if zero_variance_streak >= thresholds.min_within_gen_variance_zero_streak:
                warnings.append(self._make_warning(
                    now,
                    "dimension_within_gen_variance_zero",
                    "medium",
                    f"Dimension '{dimension}' has mean==best for {zero_variance_streak} consecutive generations",
                    current.snapshot_id,
                    f"dimension.{dimension}.within_generation_equal_streak",
                    float(zero_variance_streak),
                    float(thresholds.min_within_gen_variance_zero_streak),
                    scenarios,
                    providers,
                    releases,
                    metadata={
                        "run_id": current.run_id,
                        "dimension": dimension,
                        "streak": zero_variance_streak,
                        "series": series,
                        "best_series": best_series,
                    },
                ))

            decline = series[0] - series[-1]
            if decline >= thresholds.max_dimension_decline and _is_monotonic_decline(series):
                warnings.append(self._make_warning(
                    now,
                    "dimension_score_decline",
                    "high",
                    f"Dimension '{dimension}' declined by {decline:.2f} across the run",
                    current.snapshot_id,
                    f"dimension.{dimension}.decline",
                    decline,
                    thresholds.max_dimension_decline,
                    scenarios,
                    providers,
                    releases,
                    metadata={"run_id": current.run_id, "dimension": dimension, "series": series},
                ))

        for left, right in combinations(sorted(current.dimension_series), 2):
            left_series = current.dimension_series[left]
            right_series = current.dimension_series[right]
            if (
                len(left_series) < thresholds.min_dimension_observations
                or len(right_series) < thresholds.min_dimension_observations
            ):
                continue
            correlation = _pearson(left_series, right_series)
            if correlation is None or abs(correlation) < thresholds.max_dimension_correlation:
                continue
            warnings.append(self._make_warning(
                now,
                "dimension_correlation_high",
                "medium",
                f"Dimensions '{left}' and '{right}' move together with correlation {correlation:.2f}",
                current.snapshot_id,
                f"dimension_correlation.{left}.{right}",
                abs(correlation),
                thresholds.max_dimension_correlation,
                scenarios,
                providers,
                releases,
                metadata={
                    "run_id": current.run_id,
                    "dimensions": [left, right],
                    "correlation": round(correlation, 4),
                },
            ))

        return warnings

    def analyze(
        self,
        facets: list[RunFacet],
        release: str = "",
        scenario_family: str = "",
        agent_provider: str = "",
        baseline: RubricSnapshot | None = None,
    ) -> tuple[RubricSnapshot, list[DriftWarning]]:
        snap = self.compute_snapshot(
            facets, release=release,
            scenario_family=scenario_family,
            agent_provider=agent_provider,
        )
        warnings = self.detect_drift(snap, baseline=baseline)
        return snap, warnings

    def _make_warning(
        self,
        now: str,
        warning_type: str,
        severity: str,
        description: str,
        snapshot_id: str,
        metric_name: str,
        metric_value: float,
        threshold_value: float,
        scenarios: list[str],
        providers: list[str],
        releases: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> DriftWarning:
        return DriftWarning(
            warning_id=f"warn-{uuid.uuid4().hex[:8]}",
            created_at=now,
            warning_type=warning_type,
            severity=severity,
            description=description,
            snapshot_id=snapshot_id,
            metric_name=metric_name,
            metric_value=round(metric_value, 4),
            threshold_value=round(threshold_value, 4),
            affected_scenarios=scenarios,
            affected_providers=providers,
            affected_releases=releases,
            metadata=metadata or {},
        )


def _dimension_summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("dimension_summary")
    if isinstance(summary, dict):
        return summary
    raw_summary = row.get("dimension_summary_json")
    if isinstance(raw_summary, str) and raw_summary:
        try:
            parsed = json.loads(raw_summary)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _append_dimension_values(target: dict[str, list[float]], raw_values: Any) -> None:
    if not isinstance(raw_values, dict):
        return
    for raw_dimension, raw_score in raw_values.items():
        if not isinstance(raw_dimension, str) or not isinstance(raw_score, (int, float)):
            continue
        target.setdefault(raw_dimension, []).append(round(float(raw_score), 6))


def _is_monotonic_decline(series: list[float]) -> bool:
    return all(right <= left for left, right in zip(series, series[1:], strict=False))


def _max_equal_streak(left: list[float], right: list[float], *, limit: int) -> int:
    max_streak = 0
    current_streak = 0
    for left_value, right_value in zip(left, right, strict=False):
        if abs(left_value - right_value) <= 1e-9:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
            if max_streak >= limit:
                return max_streak
        else:
            current_streak = 0
    return max_streak


def _pearson(left: list[float], right: list[float]) -> float | None:
    length = min(len(left), len(right))
    if length < 2:
        return None
    left_values = left[:length]
    right_values = right[:length]
    left_mean = statistics.mean(left_values)
    right_mean = statistics.mean(right_values)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values, strict=False))
    left_var = sum((a - left_mean) ** 2 for a in left_values)
    right_var = sum((b - right_mean) ** 2 for b in right_values)
    if left_var == 0.0 or right_var == 0.0:
        return None
    return float(numerator / ((left_var * right_var) ** 0.5))


class DriftStore:
    """Persists rubric drift snapshots and warnings as JSON files."""

    def __init__(self, root: Path) -> None:
        self._snapshots_dir = root / "drift_snapshots"
        self._warnings_dir = root / "drift_warnings"
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._warnings_dir.mkdir(parents=True, exist_ok=True)

    def persist_snapshot(self, snapshot: RubricSnapshot) -> Path:
        path = self._snapshots_dir / f"{snapshot.snapshot_id}.json"
        write_json(path, snapshot.to_dict())
        return path

    def load_snapshot(self, snapshot_id: str) -> RubricSnapshot | None:
        path = self._snapshots_dir / f"{snapshot_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return RubricSnapshot.from_dict(data)

    def list_snapshots(self) -> list[RubricSnapshot]:
        results: list[RubricSnapshot] = []
        for path in sorted(self._snapshots_dir.glob("*.json")):
            data = read_json(path)
            results.append(RubricSnapshot.from_dict(data))
        return results

    def persist_warning(self, warning: DriftWarning) -> Path:
        path = self._warnings_dir / f"{warning.warning_id}.json"
        write_json(path, warning.to_dict())
        return path

    def load_warning(self, warning_id: str) -> DriftWarning | None:
        path = self._warnings_dir / f"{warning_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return DriftWarning.from_dict(data)

    def list_warnings(self) -> list[DriftWarning]:
        results: list[DriftWarning] = []
        for path in sorted(self._warnings_dir.glob("*.json")):
            data = read_json(path)
            results.append(DriftWarning.from_dict(data))
        return results
