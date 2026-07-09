"""Periodic human calibration and spot-check workflow (AC-260).

Defines a lightweight sampling workflow for human review of judge rubrics
and evolving playbooks. High-risk cases (large score jumps, near-perfect
scores, contradictory rubric satisfaction) are prioritized for review.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autocontext.analytics.facets import RunFacet
from autocontext.util.json_io import read_json, write_json

# Score threshold for "near-perfect"
_PERFECT_THRESHOLD = 0.95


class CalibrationSample(BaseModel):
    """A run selected for human calibration review."""

    sample_id: str
    run_id: str
    scenario: str
    scenario_family: str = ""
    agent_provider: str = ""
    generation_index: int = 0
    risk_score: float = 0.0
    risk_reasons: list[str] = Field(default_factory=list)
    best_score: float = 0.0
    score_delta: float = 0.0
    playbook_mutation_size: int = 0
    evaluator_epoch: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationSample:
        return cls.model_validate(data)


class CalibrationOutcome(BaseModel):
    """Human calibration decision for a sample."""

    outcome_id: str
    sample_id: str
    decision: str = ""  # approve, reject, needs_adjustment
    reviewer: str = ""
    notes: str = ""
    rubric_quality: str = ""  # good, degraded, overfit, unstable
    playbook_quality: str = ""  # good, degraded, bloated, drifted
    recommended_action: str = "none"  # none, rollback_rubric, rollback_playbook, investigate
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationOutcome:
        return cls.model_validate(data)


class CalibrationRound(BaseModel):
    """A periodic calibration round with samples and outcomes."""

    round_id: str
    created_at: str
    samples: list[CalibrationSample] = Field(default_factory=list)
    outcomes: list[CalibrationOutcome] = Field(default_factory=list)
    status: str = "pending"  # pending, in_progress, completed
    summary: str = ""
    mixed_epoch: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationRound:
        return cls.model_validate(data)


def compute_round_mixed_epoch(samples: list[CalibrationSample]) -> bool:
    """True when the samples span more than one evaluator class.

    None (unknown/legacy) is its own class comparable only with None, so it is
    NOT filtered out: a known epoch mixed with None spans two classes and is
    flagged, while an all-None or empty aggregate is not.
    """
    return len({s.evaluator_epoch for s in samples}) > 1


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
                evaluator_epoch=facet.evaluator_epoch,
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
        write_json(path, rnd.to_dict())
        return path

    def load_round(self, round_id: str) -> CalibrationRound | None:
        path = self._rounds_dir / f"{round_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return CalibrationRound.from_dict(data)

    def list_rounds(self) -> list[CalibrationRound]:
        results: list[CalibrationRound] = []
        for path in sorted(self._rounds_dir.glob("*.json")):
            data = read_json(path)
            results.append(CalibrationRound.from_dict(data))
        return results

    def persist_outcome(self, outcome: CalibrationOutcome) -> Path:
        path = self._outcomes_dir / f"{outcome.outcome_id}.json"
        write_json(path, outcome.to_dict())
        return path

    def load_outcome(self, outcome_id: str) -> CalibrationOutcome | None:
        path = self._outcomes_dir / f"{outcome_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return CalibrationOutcome.from_dict(data)

    def list_outcomes(self) -> list[CalibrationOutcome]:
        results: list[CalibrationOutcome] = []
        for path in sorted(self._outcomes_dir.glob("*.json")):
            data = read_json(path)
            results.append(CalibrationOutcome.from_dict(data))
        return results
