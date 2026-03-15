"""Pattern clustering across runs (AC-256).

Groups similar friction and delight signals across RunFacets,
supporting sequence-level pattern detection and queryable clusters.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from autocontext.analytics.facets import RunFacet


@dataclass(slots=True)
class EventPattern:
    """A recurring event or sequence pattern across runs."""

    pattern_id: str
    pattern_type: str  # single_event, sequence, motif
    description: str
    event_sequence: list[str]
    frequency: int
    run_ids: list[str]
    confidence: float
    evidence: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "description": self.description,
            "event_sequence": self.event_sequence,
            "frequency": self.frequency,
            "run_ids": self.run_ids,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventPattern:
        return cls(
            pattern_id=data["pattern_id"],
            pattern_type=data["pattern_type"],
            description=data["description"],
            event_sequence=data.get("event_sequence", []),
            frequency=data.get("frequency", 0),
            run_ids=data.get("run_ids", []),
            confidence=data.get("confidence", 0.0),
            evidence=data.get("evidence", []),
        )


@dataclass(slots=True)
class FacetCluster:
    """A group of similar friction or delight signals across runs."""

    cluster_id: str
    label: str
    category: str  # friction or delight
    signal_types: list[str]
    run_ids: list[str]
    frequency: int
    recurrence_rate: float
    confidence: float
    evidence_summary: str
    supporting_events: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label,
            "category": self.category,
            "signal_types": self.signal_types,
            "run_ids": self.run_ids,
            "frequency": self.frequency,
            "recurrence_rate": self.recurrence_rate,
            "confidence": self.confidence,
            "evidence_summary": self.evidence_summary,
            "supporting_events": self.supporting_events,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FacetCluster:
        return cls(
            cluster_id=data["cluster_id"],
            label=data["label"],
            category=data["category"],
            signal_types=data.get("signal_types", []),
            run_ids=data.get("run_ids", []),
            frequency=data.get("frequency", 0),
            recurrence_rate=data.get("recurrence_rate", 0.0),
            confidence=data.get("confidence", 0.0),
            evidence_summary=data.get("evidence_summary", ""),
            supporting_events=data.get("supporting_events", []),
            metadata=data.get("metadata", {}),
        )


class PatternClusterer:
    """Groups similar friction/delight patterns across runs."""

    def cluster_friction(self, facets: list[RunFacet]) -> list[FacetCluster]:
        """Cluster friction signals by signal_type across facets."""
        if not facets:
            return []
        return self._cluster_signals(facets, "friction")

    def cluster_delight(self, facets: list[RunFacet]) -> list[FacetCluster]:
        """Cluster delight signals by signal_type across facets."""
        if not facets:
            return []
        return self._cluster_signals(facets, "delight")

    def _cluster_signals(
        self, facets: list[RunFacet], category: str
    ) -> list[FacetCluster]:
        # Group signals by type across all facets
        type_to_runs: dict[str, set[str]] = defaultdict(set)
        type_to_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
        type_to_facets: dict[str, list[RunFacet]] = defaultdict(list)

        for facet in facets:
            signals = (
                facet.friction_signals if category == "friction"
                else facet.delight_signals
            )
            seen_types: set[str] = set()
            for signal in signals:
                st = signal.signal_type
                type_to_runs[st].add(facet.run_id)
                type_to_evidence[st].append({
                    "run_id": facet.run_id,
                    "generation_index": signal.generation_index,
                    "description": signal.description,
                })
                if st not in seen_types:
                    seen_types.add(st)
                    type_to_facets[st].append(facet)

        total_runs = len(facets)
        clusters: list[FacetCluster] = []

        for signal_type, run_ids in type_to_runs.items():
            frequency = len(run_ids)
            recurrence_rate = frequency / total_runs if total_runs > 0 else 0.0
            confidence = min(1.0, recurrence_rate + 0.1 * frequency)

            clusters.append(FacetCluster(
                cluster_id=f"clust-{uuid.uuid4().hex[:8]}",
                label=f"Recurring {signal_type}",
                category=category,
                signal_types=[signal_type],
                run_ids=sorted(run_ids),
                frequency=frequency,
                recurrence_rate=round(recurrence_rate, 4),
                confidence=round(confidence, 4),
                evidence_summary=f"{frequency} of {total_runs} runs exhibited {signal_type}",
                supporting_events=type_to_evidence[signal_type][:5],
                metadata={
                    "scenarios": sorted({
                        f.scenario for f in type_to_facets[signal_type]
                    }),
                    "scenario_families": sorted({
                        f.scenario_family for f in type_to_facets[signal_type]
                        if f.scenario_family
                    }),
                    "providers": sorted({
                        f.agent_provider for f in type_to_facets[signal_type]
                    }),
                    "releases": sorted({
                        str(f.metadata.get("release", ""))
                        for f in type_to_facets[signal_type]
                        if f.metadata.get("release", "")
                    }),
                },
            ))

        return sorted(clusters, key=lambda c: c.frequency, reverse=True)

    def query_clusters(
        self,
        clusters: list[FacetCluster],
        scenario: str | None = None,
        agent_provider: str | None = None,
        scenario_family: str | None = None,
    ) -> list[FacetCluster]:
        """Filter clusters by metadata dimensions."""
        results: list[FacetCluster] = []
        for cluster in clusters:
            if scenario is not None:
                scenarios = cluster.metadata.get("scenarios", [])
                if scenario not in scenarios:
                    continue
            if agent_provider is not None:
                providers = cluster.metadata.get("providers", [])
                if agent_provider not in providers:
                    continue
            if scenario_family is not None:
                families = cluster.metadata.get("scenario_families", [])
                if scenario_family not in families:
                    continue
            results.append(cluster)
        return results
