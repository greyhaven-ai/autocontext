"""Thresholded issue and probe generation from friction patterns (AC-257).

Generates auditable IssueCandidate and ProbeCandidate instances from
correlated cluster evidence, with configurable thresholds and a
require_correlation guard to prevent raw-count-driven generation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from autocontext.analytics.clustering import FacetCluster
from autocontext.analytics.correlation import CorrelationResult


@dataclass(slots=True)
class ThresholdConfig:
    """Thresholds for issue/probe candidate generation."""

    min_recurrence: int = 3
    min_confidence: float = 0.6
    min_recurrence_rate: float = 0.3
    require_correlation: bool = True


@dataclass(slots=True)
class IssueCandidate:
    """A proposed issue generated from correlated friction evidence."""

    candidate_id: str
    title: str
    description: str
    priority: str  # low, medium, high, critical
    source_cluster_ids: list[str]
    correlation_id: str
    recurrence_count: int
    confidence: float
    correlation_rationale: str
    affected_scenarios: list[str]
    affected_families: list[str]
    affected_providers: list[str]
    affected_releases: list[str]
    evidence: list[dict[str, Any]]
    created_at: str
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "source_cluster_ids": self.source_cluster_ids,
            "correlation_id": self.correlation_id,
            "recurrence_count": self.recurrence_count,
            "confidence": self.confidence,
            "correlation_rationale": self.correlation_rationale,
            "affected_scenarios": self.affected_scenarios,
            "affected_families": self.affected_families,
            "affected_providers": self.affected_providers,
            "affected_releases": self.affected_releases,
            "evidence": self.evidence,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IssueCandidate:
        return cls(
            candidate_id=data["candidate_id"],
            title=data["title"],
            description=data["description"],
            priority=data["priority"],
            source_cluster_ids=data.get("source_cluster_ids", []),
            correlation_id=data.get("correlation_id", ""),
            recurrence_count=data.get("recurrence_count", 0),
            confidence=data.get("confidence", 0.0),
            correlation_rationale=data.get("correlation_rationale", ""),
            affected_scenarios=data.get("affected_scenarios", []),
            affected_families=data.get("affected_families", []),
            affected_providers=data.get("affected_providers", []),
            affected_releases=data.get("affected_releases", []),
            evidence=data.get("evidence", []),
            created_at=data.get("created_at", ""),
            status=data.get("status", "proposed"),
        )


@dataclass(slots=True)
class ProbeCandidate:
    """A proposed probe/fixture generated from correlated friction evidence."""

    candidate_id: str
    probe_type: str  # regression_fixture, targeted_probe, seeded_variant
    title: str
    description: str
    source_cluster_ids: list[str]
    correlation_id: str
    target_scenario_family: str
    target_friction_type: str
    recurrence_count: int
    confidence: float
    correlation_rationale: str
    seed_data: dict[str, Any]
    evidence: list[dict[str, Any]]
    created_at: str
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "probe_type": self.probe_type,
            "title": self.title,
            "description": self.description,
            "source_cluster_ids": self.source_cluster_ids,
            "correlation_id": self.correlation_id,
            "target_scenario_family": self.target_scenario_family,
            "target_friction_type": self.target_friction_type,
            "recurrence_count": self.recurrence_count,
            "confidence": self.confidence,
            "correlation_rationale": self.correlation_rationale,
            "seed_data": self.seed_data,
            "evidence": self.evidence,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProbeCandidate:
        return cls(
            candidate_id=data["candidate_id"],
            probe_type=data["probe_type"],
            title=data["title"],
            description=data["description"],
            source_cluster_ids=data.get("source_cluster_ids", []),
            correlation_id=data.get("correlation_id", ""),
            target_scenario_family=data.get("target_scenario_family", ""),
            target_friction_type=data.get("target_friction_type", ""),
            recurrence_count=data.get("recurrence_count", 0),
            confidence=data.get("confidence", 0.0),
            correlation_rationale=data.get("correlation_rationale", ""),
            seed_data=data.get("seed_data", {}),
            evidence=data.get("evidence", []),
            created_at=data.get("created_at", ""),
            status=data.get("status", "proposed"),
        )


class IssueGenerator:
    """Generates issue and probe candidates from correlated cluster evidence."""

    def __init__(self, config: ThresholdConfig | None = None) -> None:
        self._config = config or ThresholdConfig()

    def generate(
        self,
        clusters: list[FacetCluster],
        correlation: CorrelationResult,
    ) -> tuple[list[IssueCandidate], list[ProbeCandidate]]:
        cfg = self._config
        now = datetime.now(UTC).isoformat()

        # Guard: if require_correlation and no meaningful dimensions, block all
        if cfg.require_correlation and not correlation.dimensions:
            return [], []

        issues: list[IssueCandidate] = []
        probes: list[ProbeCandidate] = []

        for cluster in clusters:
            if not self._meets_threshold(cluster):
                continue

            meta = cluster.metadata or {}
            scenarios = meta.get("scenarios", [])
            families = meta.get("scenario_families", [])
            providers = meta.get("providers", [])
            releases = meta.get("releases", [])

            # Build correlation rationale from dimensions
            rationale = self._build_rationale(cluster, correlation)

            # Priority from confidence/recurrence
            priority = self._compute_priority(cluster)

            issue = IssueCandidate(
                candidate_id=f"issue-{uuid.uuid4().hex[:8]}",
                title=f"Recurring {cluster.signal_types[0]} across {cluster.frequency} runs",
                description=cluster.evidence_summary,
                priority=priority,
                source_cluster_ids=[cluster.cluster_id],
                correlation_id=correlation.correlation_id,
                recurrence_count=cluster.frequency,
                confidence=cluster.confidence,
                correlation_rationale=rationale,
                affected_scenarios=scenarios,
                affected_families=families,
                affected_providers=providers,
                affected_releases=releases,
                evidence=cluster.supporting_events[:5],
                created_at=now,
            )
            issues.append(issue)

            # Generate a probe for each qualifying friction cluster
            if cluster.category == "friction":
                primary_family = families[0] if families else ""
                primary_type = cluster.signal_types[0] if cluster.signal_types else ""
                probe = ProbeCandidate(
                    candidate_id=f"probe-{uuid.uuid4().hex[:8]}",
                    probe_type="regression_fixture",
                    title=f"Regression fixture for {primary_type}",
                    description=f"Seeded scenario to reproduce {primary_type}",
                    source_cluster_ids=[cluster.cluster_id],
                    correlation_id=correlation.correlation_id,
                    target_scenario_family=primary_family,
                    target_friction_type=primary_type,
                    recurrence_count=cluster.frequency,
                    confidence=cluster.confidence,
                    correlation_rationale=rationale,
                    seed_data={
                        "scenarios": scenarios,
                        "providers": providers,
                        "releases": releases,
                    },
                    evidence=cluster.supporting_events[:3],
                    created_at=now,
                )
                probes.append(probe)

        return issues, probes

    def _meets_threshold(self, cluster: FacetCluster) -> bool:
        cfg = self._config
        return (
            cluster.frequency >= cfg.min_recurrence
            and cluster.confidence >= cfg.min_confidence
            and cluster.recurrence_rate >= cfg.min_recurrence_rate
        )

    def _build_rationale(
        self, cluster: FacetCluster, correlation: CorrelationResult
    ) -> str:
        parts: list[str] = []
        primary_type = cluster.signal_types[0] if cluster.signal_types else "unknown"
        parts.append(
            f"{primary_type} observed in {cluster.frequency} runs "
            f"(recurrence rate {cluster.recurrence_rate:.0%})"
        )

        for dim in correlation.dimensions:
            if primary_type in dim.top_friction_types:
                parts.append(
                    f"Concentrated in {dim.dimension}={dim.value} "
                    f"({dim.friction_count} friction signals across {dim.run_count} runs)"
                )

        if correlation.release_regressions:
            for reg in correlation.release_regressions:
                parts.append(
                    f"Regression in {reg['release']}: "
                    f"friction rate delta +{reg['delta']}"
                )

        return "; ".join(parts)

    def _compute_priority(self, cluster: FacetCluster) -> str:
        if cluster.confidence >= 0.9 and cluster.frequency >= 5:
            return "critical"
        if cluster.confidence >= 0.7 and cluster.frequency >= 3:
            return "high"
        if cluster.confidence >= 0.5:
            return "medium"
        return "low"
