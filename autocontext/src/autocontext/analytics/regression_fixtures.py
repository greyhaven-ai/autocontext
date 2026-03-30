"""Friction analytics → regression fixtures and prevalidation (AC-328).

Converts recurring friction clusters into reusable regression fixtures
that can participate in holdout or prevalidation evaluation.

Key types:
- RegressionFixture: a generated test fixture from friction evidence
- generate_fixtures_from_friction(): converts clusters into fixtures
- FixtureStore: JSON-file persistence with scenario filtering
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.util.json_io import read_json, write_json


@dataclass(slots=True)
class RegressionFixture:
    """A generated regression test fixture from friction evidence."""

    fixture_id: str
    scenario: str
    description: str
    seed: int
    strategy: dict[str, Any]
    expected_min_score: float
    source_evidence: list[str]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "scenario": self.scenario,
            "description": self.description,
            "seed": self.seed,
            "strategy": self.strategy,
            "expected_min_score": self.expected_min_score,
            "source_evidence": self.source_evidence,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionFixture:
        return cls(
            fixture_id=data["fixture_id"],
            scenario=data.get("scenario", ""),
            description=data.get("description", ""),
            seed=data.get("seed", 0),
            strategy=data.get("strategy", {}),
            expected_min_score=data.get("expected_min_score", 0.0),
            source_evidence=data.get("source_evidence", []),
            confidence=data.get("confidence", 0.0),
            metadata=data.get("metadata", {}),
        )


def generate_fixtures_from_friction(
    clusters: list[dict[str, Any]],
    scenario: str,
    min_occurrences: int = 2,
) -> list[RegressionFixture]:
    """Convert recurring friction clusters into regression fixtures."""
    if not clusters:
        return []

    fixtures: list[RegressionFixture] = []
    for cluster in clusters:
        count = int(cluster.get("count", cluster.get("frequency", 0)) or 0)
        if count < min_occurrences:
            continue

        supporting_events = cluster.get("supporting_events", [])
        generations = cluster.get("generations", [])
        if not generations and isinstance(supporting_events, list):
            generations = [
                int(event.get("generation_index", 0))
                for event in supporting_events
                if isinstance(event, dict)
            ]

        signal_types = cluster.get("signal_types", [])
        pattern = str(
            cluster.get("pattern")
            or (signal_types[0] if signal_types else "")
            or str(cluster.get("label", "Recurring unknown")).removeprefix("Recurring ").strip()
            or "unknown"
        )
        description = str(
            cluster.get("description")
            or cluster.get("evidence_summary")
            or cluster.get("label")
            or f"Recurring {pattern}"
        )
        fixture_id = _stable_fixture_id(scenario, pattern)

        fixture = RegressionFixture(
            fixture_id=fixture_id,
            scenario=scenario,
            description=description,
            seed=generations[0] * 100 if generations else 42,
            strategy=dict(cluster.get("strategy", {})) if isinstance(cluster.get("strategy"), dict) else {},
            expected_min_score=float(cluster.get("expected_min_score", 0.5) or 0.5),
            source_evidence=[
                str(entry)
                for entry in (
                    cluster.get("source_evidence")
                    or [f"friction:{pattern}:gen{g}" for g in generations]
                )
            ],
            confidence=float(cluster.get("confidence", min(1.0, count / 5.0)) or 0.0),
            metadata={
                "pattern": pattern,
                "count": count,
                "signal_types": signal_types if isinstance(signal_types, list) else [],
                "cluster_id": cluster.get("cluster_id", ""),
            },
        )
        fixtures.append(fixture)

    return fixtures


class FixtureStore:
    """JSON-file persistence for regression fixtures."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "regression_fixtures"
        self._dir.mkdir(parents=True, exist_ok=True)

    def persist(self, fixture: RegressionFixture) -> Path:
        path = self._dir / f"{fixture.fixture_id}.json"
        write_json(path, fixture.to_dict())
        return path

    def replace_for_scenario(
        self,
        scenario: str,
        fixtures: list[RegressionFixture],
    ) -> list[Path]:
        """Replace all fixtures for a scenario with the provided set."""
        retained_ids = {fixture.fixture_id for fixture in fixtures}
        for existing in self.list_for_scenario(scenario):
            if existing.fixture_id not in retained_ids:
                path = self._dir / f"{existing.fixture_id}.json"
                if path.exists():
                    path.unlink()
        return [self.persist(fixture) for fixture in fixtures]

    def load(self, fixture_id: str) -> RegressionFixture | None:
        path = self._dir / f"{fixture_id}.json"
        if not path.exists():
            return None
        return RegressionFixture.from_dict(read_json(path))

    def list_for_scenario(self, scenario: str) -> list[RegressionFixture]:
        results: list[RegressionFixture] = []
        for path in sorted(self._dir.glob("*.json")):
            fix = RegressionFixture.from_dict(read_json(path))
            if fix.scenario == scenario:
                results.append(fix)
        return results


def _stable_fixture_id(scenario: str, pattern: str) -> str:
    def slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

    scenario_slug = slug(scenario) or "scenario"
    pattern_slug = slug(pattern) or "pattern"
    return f"fix-{scenario_slug}-{pattern_slug}"
