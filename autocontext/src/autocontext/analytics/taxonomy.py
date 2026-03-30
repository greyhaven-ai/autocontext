"""Evolving facet taxonomy (AC-256).

Manages a taxonomy of facet categories that can grow as new recurring
patterns are discovered by the clustering engine.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.analytics.clustering import FacetCluster
from autocontext.util.json_io import read_json, write_json


@dataclass(slots=True)
class TaxonomyEntry:
    """An entry in the evolving facet taxonomy."""

    entry_id: str
    name: str
    parent_category: str  # friction, delight, neutral
    description: str
    is_system_defined: bool
    source_cluster_id: str | None
    created_at: str
    recurrence_count: int
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "name": self.name,
            "parent_category": self.parent_category,
            "description": self.description,
            "is_system_defined": self.is_system_defined,
            "source_cluster_id": self.source_cluster_id,
            "created_at": self.created_at,
            "recurrence_count": self.recurrence_count,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaxonomyEntry:
        return cls(
            entry_id=data["entry_id"],
            name=data["name"],
            parent_category=data["parent_category"],
            description=data["description"],
            is_system_defined=data.get("is_system_defined", False),
            source_cluster_id=data.get("source_cluster_id"),
            created_at=data.get("created_at", ""),
            recurrence_count=data.get("recurrence_count", 0),
            confidence=data.get("confidence", 0.0),
        )


# Built-in taxonomy entries
_BUILTIN_FRICTION: list[tuple[str, str]] = [
    ("validation_failure", "Validation stage failures in generated code or strategies"),
    ("retry_loop", "Backpressure gate triggered a retry"),
    ("rollback", "Backpressure gate triggered a rollback to previous generation"),
    ("stale_context", "Agent operated on stale or invalidated context"),
    ("tool_failure", "Tool invocation failed or returned unexpected results"),
    ("dependency_error", "Dependency ordering error in action execution"),
]

_BUILTIN_DELIGHT: list[tuple[str, str]] = [
    ("fast_advance", "Generation advanced on first attempt"),
    ("clean_recovery", "Recovered cleanly after a rollback or retry"),
    ("efficient_tool_use", "Effective tool usage with minimal overhead"),
    ("strong_improvement", "Large score improvement between generations"),
]


def _make_builtins() -> list[TaxonomyEntry]:
    entries: list[TaxonomyEntry] = []
    now = datetime.now(UTC).isoformat()
    for name, desc in _BUILTIN_FRICTION:
        entries.append(TaxonomyEntry(
            entry_id=f"builtin-friction-{name}",
            name=name,
            parent_category="friction",
            description=desc,
            is_system_defined=True,
            source_cluster_id=None,
            created_at=now,
            recurrence_count=0,
            confidence=1.0,
        ))
    for name, desc in _BUILTIN_DELIGHT:
        entries.append(TaxonomyEntry(
            entry_id=f"builtin-delight-{name}",
            name=name,
            parent_category="delight",
            description=desc,
            is_system_defined=True,
            source_cluster_id=None,
            created_at=now,
            recurrence_count=0,
            confidence=1.0,
        ))
    return entries


class FacetTaxonomy:
    """Evolving taxonomy of facet categories."""

    def __init__(self) -> None:
        self._entries: list[TaxonomyEntry] = _make_builtins()

    def get_entries(self) -> list[TaxonomyEntry]:
        """Return all taxonomy entries."""
        return list(self._entries)

    def add_entry(self, entry: TaxonomyEntry) -> None:
        """Add a new taxonomy entry."""
        self._entries.append(entry)

    def _has_name(self, name: str) -> bool:
        return any(e.name == name for e in self._entries)

    def propose_from_cluster(
        self,
        cluster: FacetCluster,
        min_confidence: float = 0.6,
    ) -> TaxonomyEntry | None:
        """Propose a new taxonomy entry from a cluster.

        Returns None if the cluster's confidence is below the threshold
        or if the signal type already exists in the taxonomy.
        """
        if cluster.confidence < min_confidence:
            return None

        # Use the primary signal type as the entry name
        name = cluster.signal_types[0] if cluster.signal_types else cluster.label
        if self._has_name(name):
            return None

        return TaxonomyEntry(
            entry_id=f"evolved-{uuid.uuid4().hex[:8]}",
            name=name,
            parent_category=cluster.category,
            description=cluster.evidence_summary,
            is_system_defined=False,
            source_cluster_id=cluster.cluster_id,
            created_at=datetime.now(UTC).isoformat(),
            recurrence_count=cluster.frequency,
            confidence=cluster.confidence,
        )

    def evolve(
        self,
        clusters: list[FacetCluster],
        min_confidence: float = 0.6,
    ) -> list[TaxonomyEntry]:
        """Evolve the taxonomy by proposing entries from clusters.

        Returns the list of newly added entries.
        """
        new_entries: list[TaxonomyEntry] = []
        for cluster in clusters:
            proposed = self.propose_from_cluster(cluster, min_confidence)
            if proposed is not None:
                self.add_entry(proposed)
                new_entries.append(proposed)
        return new_entries

    def save(self, path: Path) -> None:
        """Persist the taxonomy to a JSON file."""
        data = [e.to_dict() for e in self._entries]
        write_json(path, data)

    @classmethod
    def load(cls, path: Path) -> FacetTaxonomy:
        """Load a taxonomy from a JSON file."""
        taxonomy = cls()
        if path.exists():
            data = read_json(path)
            taxonomy._entries = [TaxonomyEntry.from_dict(d) for d in data]
        return taxonomy
