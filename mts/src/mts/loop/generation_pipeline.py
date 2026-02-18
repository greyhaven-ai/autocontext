"""GenerationPipeline — composed stage orchestrator for the generation loop."""
from __future__ import annotations

from typing import TYPE_CHECKING

from mts.loop.stage_types import GenerationContext
from mts.loop.stages import (
    stage_agent_generation,
    stage_curator_gate,
    stage_knowledge_setup,
    stage_persistence,
    stage_tournament,
)

if TYPE_CHECKING:
    from mts.agents.curator import KnowledgeCurator
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.backpressure import BackpressureGate
    from mts.backpressure.trend_gate import TrendAwareGate
    from mts.execution.tournament import TournamentRunner
    from mts.knowledge.trajectory import ScoreTrajectoryBuilder
    from mts.loop.events import EventStreamEmitter
    from mts.storage import ArtifactStore, SQLiteStore


class GenerationPipeline:
    """Orchestrates a single generation through decomposed stages."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        tournament_runner: TournamentRunner,
        gate: BackpressureGate | TrendAwareGate,
        artifacts: ArtifactStore,
        sqlite: SQLiteStore,
        trajectory_builder: ScoreTrajectoryBuilder,
        events: EventStreamEmitter,
        curator: KnowledgeCurator | None,
    ) -> None:
        self._orchestrator = orchestrator
        self._tournament_runner = tournament_runner
        self._gate = gate
        self._artifacts = artifacts
        self._sqlite = sqlite
        self._trajectory_builder = trajectory_builder
        self._events = events
        self._curator = curator

    def run_generation(self, ctx: GenerationContext) -> GenerationContext:
        """Execute all stages for a single generation."""

        def _on_role_event(role: str, status: str) -> None:
            self._events.emit("role_event", {
                "run_id": ctx.run_id, "generation": ctx.generation,
                "role": role, "status": status,
            })

        # Stage 1: Knowledge setup
        ctx = stage_knowledge_setup(
            ctx,
            artifacts=self._artifacts,
            trajectory_builder=self._trajectory_builder,
        )

        # Stage 2: Agent generation
        ctx = stage_agent_generation(
            ctx,
            orchestrator=self._orchestrator,
            artifacts=self._artifacts,
            sqlite=self._sqlite,
            on_role_event=_on_role_event,
        )

        # Stage 3: Tournament + gate
        ctx = stage_tournament(
            ctx,
            tournament_runner=self._tournament_runner,
            gate=self._gate,
            events=self._events,
            sqlite=self._sqlite,
            artifacts=self._artifacts,
            agents=self._orchestrator,
        )

        # Stage 4: Curator quality gate
        ctx = stage_curator_gate(
            ctx,
            curator=self._curator,
            artifacts=self._artifacts,
            trajectory_builder=self._trajectory_builder,
            sqlite=self._sqlite,
            events=self._events,
        )

        # Stage 5: Persistence
        ctx = stage_persistence(
            ctx,
            artifacts=self._artifacts,
            sqlite=self._sqlite,
            trajectory_builder=self._trajectory_builder,
            events=self._events,
            curator=self._curator,
        )

        return ctx
