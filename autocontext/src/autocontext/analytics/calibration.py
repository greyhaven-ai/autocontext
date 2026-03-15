"""Periodic human calibration and spot-check workflow (AC-260).

Defines a lightweight sampling workflow for human review of judge rubrics
and evolving playbooks. High-risk cases (large score jumps, near-perfect
scores, contradictory rubric satisfaction) are prioritized for review.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.analytics.facets import RunFacet

# Score threshold for "near-perfect"
_PERFECT_THRESHOLD = 0.95


@dataclass(slots=True)
class CalibrationSample:
    """A run selected for human calibration review."""

    sample_id: str
    run_id: str
    scenario: str
    scenario_family: str
    agent_provider: str
    generation_index: int
    risk_score: float
    risk_reasons: list[str]
    best_score: float
    score_delta: float
    playbook_mutation_size: int
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "run_id": self.run_id,
            "scenario": self.scenario,
            "scenario_family": self.scenario_family,
            "agent_provider": self.agent_provider,
            "generation_index": self.generation_index,
            "risk_score": self.risk_score,
            "risk_reasons": self.risk_reasons,
            "best_score": self.best_score,
            "score_delta": self.score_delta,
            "playbook_mutation_size": self.playbook_mutation_size,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationSample:
        return cls(
            sample_id=data["sample_id"],
            run_id=data["run_id"],
            scenario=data.get("scenario", ""),
            scenario_family=data.get("scenario_family", ""),
            agent_provider=data.get("agent_provider", ""),
            generation_index=data.get("generation_index", 0),
            risk_score=data.get("risk_score", 0.0),
            risk_reasons=data.get("risk_reasons", []),
            best_score=data.get("best_score", 0.0),
            score_delta=data.get("score_delta", 0.0),
            playbook_mutation_size=data.get("playbook_mutation_size", 0),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class CalibrationOutcome:
    """Human calibration decision for a sample."""

    outcome_id: str
    sample_id: str
    decision: str  # approve, reject, needs_adjustment
    reviewer: str
    notes: str
    rubric_quality: str  # good, degraded, overfit, unstable
    playbook_quality: str  # good, degraded, bloated, drifted
    recommended_action: str  # none, rollback_rubric, rollback_playbook, investigate
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "sample_id": self.sample_id,
            "decision": self.decision,
            "reviewer": self.reviewer,
            "notes": self.notes,
            "rubric_quality": self.rubric_quality,
            "playbook_quality": self.playbook_quality,
            "recommended_action": self.recommended_action,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationOutcome:
        return cls(
            outcome_id=data["outcome_id"],
            sample_id=data["sample_id"],
            decision=data.get("decision", ""),
            reviewer=data.get("reviewer", ""),
            notes=data.get("notes", ""),
            rubric_quality=data.get("rubric_quality", ""),
            playbook_quality=data.get("playbook_quality", ""),
            recommended_action=data.get("recommended_action", "none"),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class CalibrationRound:
    """A periodic calibration round with samples and outcomes."""

    round_id: str
    created_at: str
    samples: list[CalibrationSample]
    outcomes: list[CalibrationOutcome]
    status: str  # pending, in_progress, completed
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "created_at": self.created_at,
            "samples": [s.to_dict() for s in self.samples],
            "outcomes": [o.to_dict() for o in self.outcomes],
            "status": self.status,
            "summary": self.summary,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationRound:
        return cls(
            round_id=data["round_id"],
            created_at=data["created_at"],
            samples=[
                CalibrationSample.from_dict(s) for s in data.get("samples", [])
            ],
            outcomes=[
                CalibrationOutcome.from_dict(o) for o in data.get("outcomes", [])
            ],
            status=data.get("status", "pending"),
            summary=data.get("summary", ""),
            metadata=data.get("metadata", {}),
        )


class SpotCheckSampler:
    """Selects high-risk cases for human calibration review."""

    def __init__(self, max_samples: int = 10) -> None:
        self._max_samples = max_samples

    def sample(
        self,
        facets: list[RunFacet],
        drift_warnings: list[Any] | None = None,
    ) -> list[CalibrationSample]:
        if not facets:
            return []

        now = datetime.now(UTC).isoformat()
        warnings = drift_warnings or []

        # Build set of (scenario, provider, release) combos flagged by warnings.
        # Release is part of the scope so the same provider/family in a different
        # release window does not get boosted accidentally.
        flagged: set[tuple[str, str, str]] = set()
        for w in warnings:
            for scenario in getattr(w, "affected_scenarios", []):
                for provider in getattr(w, "affected_providers", []):
                    releases = getattr(w, "affected_releases", []) or [""]
                    for release in releases:
                        flagged.add((scenario, provider, str(release)))

        scored: list[tuple[float, CalibrationSample]] = []
        for facet in facets:
            risk_score = 0.0
            risk_reasons: list[str] = []

            # Near-perfect score
            if facet.best_score >= _PERFECT_THRESHOLD:
                risk_score += 0.4
                risk_reasons.append("near_perfect")

            # Strong improvement signals (large score jumps)
            strong_jumps = sum(
                1 for d in facet.delight_signals
                if d.signal_type == "strong_improvement"
            )
            if strong_jumps > 0:
                risk_score += 0.3 * min(strong_jumps, 3)
                risk_reasons.append("large_score_jump")

            # Contradictory: has both friction and delight signals
            if facet.friction_signals and facet.delight_signals:
                risk_score += 0.2
                risk_reasons.append("contradictory_signals")

            # High rollback count
            if facet.rollbacks > 0:
                risk_score += 0.15
                risk_reasons.append("rollback_present")

            # Boost if this run's scenario+provider is flagged by warnings
            facet_release = str(facet.metadata.get("release", ""))
            if (
                (facet.scenario, facet.agent_provider, facet_release) in flagged
                or (facet.scenario, facet.agent_provider, "") in flagged
            ):
                risk_score += 0.3
                risk_reasons.append("drift_warning_match")

            # Score delta (approximate: best_score vs 0.5 baseline)
            score_delta = max(0.0, facet.best_score - 0.5)

            sample = CalibrationSample(
                sample_id=f"sample-{uuid.uuid4().hex[:8]}",
                run_id=facet.run_id,
                scenario=facet.scenario,
                scenario_family=facet.scenario_family,
                agent_provider=facet.agent_provider,
                generation_index=facet.total_generations - 1,
                risk_score=round(risk_score, 4),
                risk_reasons=risk_reasons,
                best_score=facet.best_score,
                score_delta=round(score_delta, 4),
                playbook_mutation_size=0,
                created_at=now,
            )
            scored.append((risk_score, sample))

        # Sort by risk descending, take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[: self._max_samples]]


class CalibrationStore:
    """Persists calibration rounds and outcomes as JSON files."""

    def __init__(self, root: Path) -> None:
        self._rounds_dir = root / "calibration_rounds"
        self._outcomes_dir = root / "calibration_outcomes"
        self._rounds_dir.mkdir(parents=True, exist_ok=True)
        self._outcomes_dir.mkdir(parents=True, exist_ok=True)

    def persist_round(self, rnd: CalibrationRound) -> Path:
        path = self._rounds_dir / f"{rnd.round_id}.json"
        path.write_text(json.dumps(rnd.to_dict(), indent=2), encoding="utf-8")
        return path

    def load_round(self, round_id: str) -> CalibrationRound | None:
        path = self._rounds_dir / f"{round_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return CalibrationRound.from_dict(data)

    def list_rounds(self) -> list[CalibrationRound]:
        results: list[CalibrationRound] = []
        for path in sorted(self._rounds_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(CalibrationRound.from_dict(data))
        return results

    def persist_outcome(self, outcome: CalibrationOutcome) -> Path:
        path = self._outcomes_dir / f"{outcome.outcome_id}.json"
        path.write_text(json.dumps(outcome.to_dict(), indent=2), encoding="utf-8")
        return path

    def load_outcome(self, outcome_id: str) -> CalibrationOutcome | None:
        path = self._outcomes_dir / f"{outcome_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return CalibrationOutcome.from_dict(data)

    def list_outcomes(self) -> list[CalibrationOutcome]:
        results: list[CalibrationOutcome] = []
        for path in sorted(self._outcomes_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(CalibrationOutcome.from_dict(data))
        return results
