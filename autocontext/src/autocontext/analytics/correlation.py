"""Signal correlation across runs (AC-258).

Correlates aggregate friction/delight clusters with releases, runtime
environment, and scenario-family dimensions.  Produces queryable
CorrelationResult artifacts that are persisted for downstream issue
and probe generation.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.analytics.clustering import FacetCluster
from autocontext.analytics.facets import RunFacet
from autocontext.util.json_io import read_json, write_json


@dataclass(slots=True)
class ReleaseContext:
    """Metadata about a software release for regression detection."""

    version: str
    released_at: str
    commit_hash: str = ""
    change_summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "released_at": self.released_at,
            "commit_hash": self.commit_hash,
            "change_summary": self.change_summary,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReleaseContext:
        return cls(
            version=data["version"],
            released_at=data["released_at"],
            commit_hash=data.get("commit_hash", ""),
            change_summary=data.get("change_summary", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class CorrelationDimension:
    """A single dimension breakdown in a correlation result."""

    dimension: str  # agent_provider, scenario, scenario_family, release
    value: str
    friction_count: int
    delight_count: int
    run_count: int
    top_friction_types: list[str]
    top_delight_types: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "friction_count": self.friction_count,
            "delight_count": self.delight_count,
            "run_count": self.run_count,
            "top_friction_types": self.top_friction_types,
            "top_delight_types": self.top_delight_types,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrelationDimension:
        return cls(
            dimension=data["dimension"],
            value=data["value"],
            friction_count=data.get("friction_count", 0),
            delight_count=data.get("delight_count", 0),
            run_count=data.get("run_count", 0),
            top_friction_types=data.get("top_friction_types", []),
            top_delight_types=data.get("top_delight_types", []),
        )


@dataclass(slots=True)
class CorrelationResult:
    """Aggregate correlation of friction/delight signals across runs."""

    correlation_id: str
    created_at: str
    total_runs: int
    total_friction: int
    total_delight: int
    dimensions: list[CorrelationDimension]
    release_regressions: list[dict[str, Any]]
    cluster_ids: list[str]
    facet_run_ids: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "total_runs": self.total_runs,
            "total_friction": self.total_friction,
            "total_delight": self.total_delight,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "release_regressions": self.release_regressions,
            "cluster_ids": self.cluster_ids,
            "facet_run_ids": self.facet_run_ids,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrelationResult:
        return cls(
            correlation_id=data["correlation_id"],
            created_at=data["created_at"],
            total_runs=data.get("total_runs", 0),
            total_friction=data.get("total_friction", 0),
            total_delight=data.get("total_delight", 0),
            dimensions=[
                CorrelationDimension.from_dict(d)
                for d in data.get("dimensions", [])
            ],
            release_regressions=data.get("release_regressions", []),
            cluster_ids=data.get("cluster_ids", []),
            facet_run_ids=data.get("facet_run_ids", []),
            metadata=data.get("metadata", {}),
        )


class SignalCorrelator:
    """Correlates friction/delight clusters with dimensional context."""

    def correlate(
        self,
        facets: list[RunFacet],
        clusters: list[FacetCluster],
        releases: list[ReleaseContext],
    ) -> CorrelationResult:
        if not facets:
            return CorrelationResult(
                correlation_id=f"corr-{uuid.uuid4().hex[:8]}",
                created_at=datetime.now(UTC).isoformat(),
                total_runs=0,
                total_friction=0,
                total_delight=0,
                dimensions=[],
                release_regressions=[],
                cluster_ids=[],
                facet_run_ids=[],
            )

        total_friction = sum(len(f.friction_signals) for f in facets)
        total_delight = sum(len(f.delight_signals) for f in facets)
        cluster_ids = [c.cluster_id for c in clusters]
        facet_run_ids = sorted({f.run_id for f in facets})

        dimensions: list[CorrelationDimension] = []
        dimensions.extend(self._dimension_breakdown(facets, "agent_provider"))
        dimensions.extend(self._dimension_breakdown(facets, "scenario"))
        dimensions.extend(self._dimension_breakdown(facets, "scenario_family"))

        # Release dimension
        release_regressions: list[dict[str, Any]] = []
        if releases:
            release_dims, regressions = self._release_breakdown(facets, releases)
            dimensions.extend(release_dims)
            release_regressions = regressions

        return CorrelationResult(
            correlation_id=f"corr-{uuid.uuid4().hex[:8]}",
            created_at=datetime.now(UTC).isoformat(),
            total_runs=len(facets),
            total_friction=total_friction,
            total_delight=total_delight,
            dimensions=dimensions,
            release_regressions=release_regressions,
            cluster_ids=cluster_ids,
            facet_run_ids=facet_run_ids,
        )

    def _dimension_breakdown(
        self, facets: list[RunFacet], dim_attr: str
    ) -> list[CorrelationDimension]:
        groups: dict[str, list[RunFacet]] = defaultdict(list)
        for f in facets:
            val = getattr(f, dim_attr, "")
            if val:
                groups[val].append(f)

        dims: list[CorrelationDimension] = []
        for value, group_facets in sorted(groups.items()):
            friction_count = sum(len(f.friction_signals) for f in group_facets)
            delight_count = sum(len(f.delight_signals) for f in group_facets)

            friction_types: dict[str, int] = defaultdict(int)
            delight_types: dict[str, int] = defaultdict(int)
            for f in group_facets:
                for fs in f.friction_signals:
                    friction_types[fs.signal_type] += 1
                for ds in f.delight_signals:
                    delight_types[ds.signal_type] += 1

            dims.append(CorrelationDimension(
                dimension=dim_attr,
                value=value,
                friction_count=friction_count,
                delight_count=delight_count,
                run_count=len(group_facets),
                top_friction_types=sorted(
                    friction_types, key=lambda k: friction_types[k], reverse=True
                )[:5],
                top_delight_types=sorted(
                    delight_types, key=lambda k: delight_types[k], reverse=True
                )[:5],
            ))
        return dims

    def _release_breakdown(
        self,
        facets: list[RunFacet],
        releases: list[ReleaseContext],
    ) -> tuple[list[CorrelationDimension], list[dict[str, Any]]]:
        release_map: dict[str, str] = {}
        for f in facets:
            rel = f.metadata.get("release", "")
            if rel:
                release_map[f.run_id] = rel

        groups: dict[str, list[RunFacet]] = defaultdict(list)
        for f in facets:
            rel = release_map.get(f.run_id, "")
            if rel:
                groups[rel].append(f)

        dims: list[CorrelationDimension] = []
        per_release_rates: dict[str, float] = {}
        for version, group_facets in sorted(groups.items()):
            friction_count = sum(len(f.friction_signals) for f in group_facets)
            delight_count = sum(len(f.delight_signals) for f in group_facets)
            run_count = len(group_facets)
            per_release_rates[version] = friction_count / run_count if run_count else 0.0

            friction_types: dict[str, int] = defaultdict(int)
            delight_types: dict[str, int] = defaultdict(int)
            for f in group_facets:
                for fs in f.friction_signals:
                    friction_types[fs.signal_type] += 1
                for ds in f.delight_signals:
                    delight_types[ds.signal_type] += 1

            dims.append(CorrelationDimension(
                dimension="release",
                value=version,
                friction_count=friction_count,
                delight_count=delight_count,
                run_count=run_count,
                top_friction_types=sorted(
                    friction_types, key=lambda k: friction_types[k], reverse=True
                )[:5],
                top_delight_types=sorted(
                    delight_types, key=lambda k: delight_types[k], reverse=True
                )[:5],
            ))

        # Detect regressions: compare sequential releases
        regressions: list[dict[str, Any]] = []
        sorted_releases = sorted(releases, key=lambda r: r.released_at)
        for i in range(1, len(sorted_releases)):
            prev_ver = sorted_releases[i - 1].version
            curr_ver = sorted_releases[i].version
            prev_rate = per_release_rates.get(prev_ver, 0.0)
            curr_rate = per_release_rates.get(curr_ver, 0.0)
            delta = curr_rate - prev_rate
            if delta > 0:
                regressions.append({
                    "release": curr_ver,
                    "previous_release": prev_ver,
                    "metric": "friction_rate",
                    "delta": round(delta, 4),
                    "current_rate": round(curr_rate, 4),
                    "previous_rate": round(prev_rate, 4),
                })

        return dims, regressions


class CorrelationStore:
    """Persists and loads CorrelationResult artifacts as JSON files."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "correlations"
        self._dir.mkdir(parents=True, exist_ok=True)

    def persist(self, result: CorrelationResult) -> Path:
        path = self._dir / f"{result.correlation_id}.json"
        write_json(path, result.to_dict())
        return path

    def load(self, correlation_id: str) -> CorrelationResult | None:
        path = self._dir / f"{correlation_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return CorrelationResult.from_dict(data)

    def list_results(self) -> list[CorrelationResult]:
        results: list[CorrelationResult] = []
        for path in sorted(self._dir.glob("*.json")):
            data = read_json(path)
            results.append(CorrelationResult.from_dict(data))
        return results
