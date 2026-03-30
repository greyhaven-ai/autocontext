"""Facet persistence and querying (AC-255).

Stores RunFacet instances as JSON files in a structured directory,
supporting listing and filtering by scenario, provider, etc.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.analytics.facets import RunFacet
from autocontext.util.json_io import read_json

_FACETS_DIR = "facets"


class FacetStore:
    """Persists and queries RunFacet instances."""

    def __init__(self, root: Path) -> None:
        self.root = root / _FACETS_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def persist(self, facet: RunFacet) -> Path:
        """Persist a RunFacet as a JSON file. Returns the file path."""
        path = self.root / f"{facet.run_id}.json"
        path.write_text(
            json.dumps(facet.to_dict(), indent=2),
            encoding="utf-8",
        )
        return path

    def load(self, run_id: str) -> RunFacet | None:
        """Load a RunFacet by run_id. Returns None if not found."""
        path = self.root / f"{run_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return RunFacet.from_dict(data)

    def list_facets(self, scenario: str | None = None) -> list[RunFacet]:
        """List all persisted facets, optionally filtered by scenario."""
        facets: list[RunFacet] = []
        for path in sorted(self.root.glob("*.json")):
            data = read_json(path)
            facet = RunFacet.from_dict(data)
            if scenario is not None and facet.scenario != scenario:
                continue
            facets.append(facet)
        return facets

    def query(self, **filters: Any) -> list[RunFacet]:
        """Query facets by arbitrary field filters.

        Supported filters: scenario, scenario_family, agent_provider,
        executor_mode.
        """
        facets = self.list_facets()
        results: list[RunFacet] = []
        for facet in facets:
            match = True
            for key, value in filters.items():
                if getattr(facet, key, None) != value:
                    match = False
                    break
            if match:
                results.append(facet)
        return results
