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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.analytics.facets import RunFacet

# Score at or above this is considered "near-perfect"
_PERFECT_THRESHOLD = 0.95


@dataclass(slots=True)
class RubricSnapshot:
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
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "run_count": self.run_count,
            "mean_score": self.mean_score,
            "median_score": self.median_score,
            "stddev_score": self.stddev_score,
            "min_score": self.min_score,
            "max_score": self.max_score,
            "score_inflation_rate": self.score_inflation_rate,
            "perfect_score_rate": self.perfect_score_rate,
            "revision_jump_rate": self.revision_jump_rate,
            "retry_rate": self.retry_rate,
            "rollback_rate": self.rollback_rate,
            "release": self.release,
            "scenario_family": self.scenario_family,
            "agent_provider": self.agent_provider,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RubricSnapshot:
        return cls(
            snapshot_id=data["snapshot_id"],
            created_at=data["created_at"],
            window_start=data.get("window_start", ""),
            window_end=data.get("window_end", ""),
            run_count=data.get("run_count", 0),
            mean_score=data.get("mean_score", 0.0),
            median_score=data.get("median_score", 0.0),
            stddev_score=data.get("stddev_score", 0.0),
            min_score=data.get("min_score", 0.0),
            max_score=data.get("max_score", 0.0),
            score_inflation_rate=data.get("score_inflation_rate", 0.0),
            perfect_score_rate=data.get("perfect_score_rate", 0.0),
            revision_jump_rate=data.get("revision_jump_rate", 0.0),
            retry_rate=data.get("retry_rate", 0.0),
            rollback_rate=data.get("rollback_rate", 0.0),
            release=data.get("release", ""),
            scenario_family=data.get("scenario_family", ""),
            agent_provider=data.get("agent_provider", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class DriftThresholds:
    """Configurable thresholds for drift detection."""

    max_score_inflation: float = 0.15
    max_perfect_rate: float = 0.5
    max_revision_jump_rate: float = 0.4
    min_stddev: float = 0.05
    max_retry_rate: float = 0.5
    max_rollback_rate: float = 0.3


@dataclass(slots=True)
class DriftWarning:
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
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "warning_id": self.warning_id,
            "created_at": self.created_at,
            "warning_type": self.warning_type,
            "severity": self.severity,
            "description": self.description,
            "snapshot_id": self.snapshot_id,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "threshold_value": self.threshold_value,
            "affected_scenarios": self.affected_scenarios,
            "affected_providers": self.affected_providers,
            "affected_releases": self.affected_releases,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DriftWarning:
        return cls(
            warning_id=data["warning_id"],
            created_at=data["created_at"],
            warning_type=data["warning_type"],
            severity=data.get("severity", "medium"),
            description=data.get("description", ""),
            snapshot_id=data.get("snapshot_id", ""),
            metric_name=data.get("metric_name", ""),
            metric_value=data.get("metric_value", 0.0),
            threshold_value=data.get("threshold_value", 0.0),
            affected_scenarios=data.get("affected_scenarios", []),
            affected_providers=data.get("affected_providers", []),
            affected_releases=data.get("affected_releases", []),
            metadata=data.get("metadata", {}),
        )


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
        )


class DriftStore:
    """Persists rubric drift snapshots and warnings as JSON files."""

    def __init__(self, root: Path) -> None:
        self._snapshots_dir = root / "drift_snapshots"
        self._warnings_dir = root / "drift_warnings"
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        self._warnings_dir.mkdir(parents=True, exist_ok=True)

    def persist_snapshot(self, snapshot: RubricSnapshot) -> Path:
        path = self._snapshots_dir / f"{snapshot.snapshot_id}.json"
        path.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
        return path

    def load_snapshot(self, snapshot_id: str) -> RubricSnapshot | None:
        path = self._snapshots_dir / f"{snapshot_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return RubricSnapshot.from_dict(data)

    def list_snapshots(self) -> list[RubricSnapshot]:
        results: list[RubricSnapshot] = []
        for path in sorted(self._snapshots_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(RubricSnapshot.from_dict(data))
        return results

    def persist_warning(self, warning: DriftWarning) -> Path:
        path = self._warnings_dir / f"{warning.warning_id}.json"
        path.write_text(json.dumps(warning.to_dict(), indent=2), encoding="utf-8")
        return path

    def load_warning(self, warning_id: str) -> DriftWarning | None:
        path = self._warnings_dir / f"{warning_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return DriftWarning.from_dict(data)

    def list_warnings(self) -> list[DriftWarning]:
        results: list[DriftWarning] = []
        for path in sorted(self._warnings_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(DriftWarning.from_dict(data))
        return results
