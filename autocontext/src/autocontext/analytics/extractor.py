"""Facet extraction from completed run data (AC-255).

Processes run metadata from SQLiteStore + ArtifactStore into structured
RunFacet instances with friction/delight signal detection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from autocontext.analytics.facets import (
    DelightSignal,
    FrictionSignal,
    RunFacet,
)


class FacetExtractor:
    """Extracts structured facets from completed run data."""

    def extract(self, data: dict[str, Any]) -> RunFacet:
        """Build a RunFacet from run data dict.

        Expects keys: run, generations, role_metrics,
        staged_validations, consultations, recovery.
        """
        run = data["run"]
        generations = data.get("generations", [])
        role_metrics = data.get("role_metrics", [])
        staged_validations = data.get("staged_validations", [])
        consultations = data.get("consultations", [])
        recovery = data.get("recovery", [])

        # Gate decision counts
        advances = sum(1 for g in generations if g.get("gate_decision") == "advance")
        retries = sum(1 for g in generations if g.get("gate_decision") == "retry")
        rollbacks = sum(1 for g in generations if g.get("gate_decision") == "rollback")

        # Best score/elo
        best_score = max((g.get("best_score", 0.0) for g in generations), default=0.0)
        best_elo = max((g.get("elo", 0.0) for g in generations), default=0.0)

        # Duration
        total_duration = sum(g.get("duration_seconds", 0.0) for g in generations)

        # Token totals
        total_tokens = sum(
            m.get("input_tokens", 0) + m.get("output_tokens", 0)
            for m in role_metrics
        )

        # Validation failures
        validation_failures = sum(
            1 for v in staged_validations if v.get("status") == "failed"
        )

        # Consultations
        consultation_count = len(consultations)
        consultation_cost = sum(c.get("cost_usd", 0.0) for c in consultations)

        # Scenario family detection (best-effort from run metadata)
        scenario_family = run.get("scenario_family", "")

        # Signal extraction
        friction_signals = self._extract_friction(
            generations, staged_validations, recovery
        )
        delight_signals = self._extract_delight(generations)

        return RunFacet(
            run_id=run["run_id"],
            scenario=run.get("scenario", ""),
            scenario_family=scenario_family,
            agent_provider=run.get("agent_provider", ""),
            executor_mode=run.get("executor_mode", ""),
            total_generations=len(generations),
            advances=advances,
            retries=retries,
            rollbacks=rollbacks,
            best_score=best_score,
            best_elo=best_elo,
            total_duration_seconds=total_duration,
            total_tokens=total_tokens,
            total_cost_usd=0.0,  # computed from provider pricing if available
            tool_invocations=0,
            validation_failures=validation_failures,
            consultation_count=consultation_count,
            consultation_cost_usd=consultation_cost,
            friction_signals=friction_signals,
            delight_signals=delight_signals,
            events=[],
            metadata=run.get("metadata", {}),
            created_at=datetime.now(UTC).isoformat(),
        )

    def _extract_friction(
        self,
        generations: list[dict[str, Any]],
        staged_validations: list[dict[str, Any]],
        recovery: list[dict[str, Any]],
    ) -> list[FrictionSignal]:
        signals: list[FrictionSignal] = []

        # Validation failures
        for v in staged_validations:
            if v.get("status") == "failed":
                signals.append(FrictionSignal(
                    signal_type="validation_failure",
                    severity="medium",
                    generation_index=v.get("generation_index", 0),
                    description=f"Validation failure in stage '{v.get('stage_name', 'unknown')}': "
                                f"{v.get('error', 'unknown error')}",
                    evidence=[f"staged_validation:{v.get('stage_name', '')}"],
                ))

        # Retry loops
        for r in recovery:
            if r.get("decision") == "retry":
                signals.append(FrictionSignal(
                    signal_type="retry_loop",
                    severity="low",
                    generation_index=r.get("generation_index", 0),
                    description=f"Retry at generation {r.get('generation_index', '?')}: "
                                f"{r.get('reason', 'unknown')}",
                    evidence=[f"recovery:{r.get('generation_index', '')}"],
                ))

        # Rollbacks
        for g in generations:
            if g.get("gate_decision") == "rollback":
                signals.append(FrictionSignal(
                    signal_type="rollback",
                    severity="high",
                    generation_index=g.get("generation_index", 0),
                    description=f"Rollback at generation {g.get('generation_index', '?')}",
                    evidence=[f"generation:{g.get('generation_index', '')}"],
                    recoverable=True,
                ))

        return signals

    def _extract_delight(
        self,
        generations: list[dict[str, Any]],
    ) -> list[DelightSignal]:
        signals: list[DelightSignal] = []

        for g in generations:
            gen_idx = g.get("generation_index", 0)

            # Fast advance: first attempt advances
            if g.get("gate_decision") == "advance":
                signals.append(DelightSignal(
                    signal_type="fast_advance",
                    generation_index=gen_idx,
                    description=f"Advanced at generation {gen_idx}",
                    evidence=[f"generation:{gen_idx}"],
                ))

        # Strong improvement: large score jumps between consecutive generations
        for i in range(1, len(generations)):
            prev_score = generations[i - 1].get("best_score", 0.0)
            curr_score = generations[i].get("best_score", 0.0)
            if curr_score - prev_score >= 0.2:
                signals.append(DelightSignal(
                    signal_type="strong_improvement",
                    generation_index=generations[i].get("generation_index", i),
                    description=f"Score improved by {curr_score - prev_score:.2f} "
                                f"({prev_score:.2f} → {curr_score:.2f})",
                    evidence=[
                        f"generation:{generations[i - 1].get('generation_index', i - 1)}",
                        f"generation:{generations[i].get('generation_index', i)}",
                    ],
                ))

        return signals
