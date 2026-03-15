"""Aggregate analysis pipeline runner (AC-258 + AC-257).

Orchestrates the full pipeline: load facets → cluster → correlate →
persist correlation → generate issues/probes → dedup → persist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from autocontext.analytics.clustering import PatternClusterer
from autocontext.analytics.correlation import (
    CorrelationResult,
    CorrelationStore,
    ReleaseContext,
    SignalCorrelator,
)
from autocontext.analytics.facets import RunFacet
from autocontext.analytics.issue_generator import (
    IssueCandidate,
    IssueGenerator,
    ProbeCandidate,
    ThresholdConfig,
)
from autocontext.analytics.issue_store import IssueStore
from autocontext.analytics.store import FacetStore


@dataclass(slots=True)
class AggregateResult:
    """Result of a full aggregate analysis pipeline run."""

    correlation: CorrelationResult
    issues: list[IssueCandidate] = field(default_factory=list)
    probes: list[ProbeCandidate] = field(default_factory=list)


class AggregateRunner:
    """Runs the full aggregate analysis pipeline end-to-end."""

    def __init__(
        self,
        facet_store: FacetStore,
        correlation_store: CorrelationStore,
        issue_store: IssueStore,
    ) -> None:
        self._facet_store = facet_store
        self._correlation_store = correlation_store
        self._issue_store = issue_store

    def run(
        self,
        release_context: list[ReleaseContext] | None = None,
        threshold_config: ThresholdConfig | None = None,
    ) -> AggregateResult:
        # 1. Load all facets
        facets = self._facet_store.list_facets()
        releases = release_context or self._derive_release_context(facets)
        config = threshold_config or ThresholdConfig()

        # 2. Cluster friction and delight
        clusterer = PatternClusterer()
        friction_clusters = clusterer.cluster_friction(facets)
        delight_clusters = clusterer.cluster_delight(facets)
        all_clusters = friction_clusters + delight_clusters

        # 3. Correlate
        correlator = SignalCorrelator()
        correlation = correlator.correlate(facets, all_clusters, releases)

        # 4. Persist correlation
        self._correlation_store.persist(correlation)

        # 5. Generate issues/probes
        generator = IssueGenerator(config)
        candidates, probes = generator.generate(all_clusters, correlation)

        # 6. Dedup by signal type (cluster IDs are non-deterministic across runs)
        new_issues: list[IssueCandidate] = []
        for candidate in candidates:
            signal_type = candidate.title.split(" across ")[0].replace("Recurring ", "")
            if not self._issue_store.has_issue_for_signature(
                signal_type=signal_type,
                scenarios=candidate.affected_scenarios,
                families=candidate.affected_families,
                providers=candidate.affected_providers,
                releases=candidate.affected_releases,
            ):
                self._issue_store.persist_issue(candidate)
                new_issues.append(candidate)

        new_probes: list[ProbeCandidate] = []
        for probe in probes:
            if not self._issue_store.has_probe_for_signature(
                signal_type=probe.target_friction_type,
                family=probe.target_scenario_family,
                scenarios=probe.seed_data.get("scenarios", []),
                providers=probe.seed_data.get("providers", []),
                releases=probe.seed_data.get("releases", []),
            ):
                self._issue_store.persist_probe(probe)
                new_probes.append(probe)

        return AggregateResult(
            correlation=correlation,
            issues=new_issues,
            probes=new_probes,
        )

    def _derive_release_context(self, facets: Sequence[RunFacet]) -> list[ReleaseContext]:
        """Derive release context from persisted facet metadata when no external feed is provided."""
        release_to_timestamp: dict[str, str] = {}
        for facet in facets:
            release = getattr(facet, "metadata", {}).get("release", "")
            if not release:
                continue
            created_at = getattr(facet, "created_at", "") or datetime.now(UTC).isoformat()
            existing = release_to_timestamp.get(release)
            if existing is None or created_at < existing:
                release_to_timestamp[release] = created_at
        return [
            ReleaseContext(version=version, released_at=released_at)
            for version, released_at in sorted(
                release_to_timestamp.items(),
                key=lambda item: item[1],
            )
        ]
