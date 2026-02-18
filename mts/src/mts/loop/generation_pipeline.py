"""GenerationPipeline — composed stage orchestrator for the generation loop."""
from __future__ import annotations

from collections.abc import Callable
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
    from mts.harness.core.controller import LoopController
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
        controller: LoopController | None = None,
        warm_provision_fn: Callable[..., dict] | None = None,
        chat_with_agent_fn: Callable[[str, str, object, str], str] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._tournament_runner = tournament_runner
        self._gate = gate
        self._artifacts = artifacts
        self._sqlite = sqlite
        self._trajectory_builder = trajectory_builder
        self._events = events
        self._curator = curator
        self._controller = controller
        self._warm_provision_fn = warm_provision_fn
        self._chat_with_agent_fn = chat_with_agent_fn

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

        # Hook: PrimeIntellect warm provision
        if self._warm_provision_fn is not None:
            warm_state = self._warm_provision_fn(ctx)
            self._events.emit("primeintellect_warm_state", {
                "run_id": ctx.run_id, "generation": ctx.generation, **warm_state,
            })

        # Stage 2: Agent generation
        ctx = stage_agent_generation(
            ctx,
            orchestrator=self._orchestrator,
            artifacts=self._artifacts,
            sqlite=self._sqlite,
            on_role_event=_on_role_event,
            events=self._events,
        )

        # Hook: Controller chat checkpoint
        if self._controller is not None and self._chat_with_agent_fn is not None:
            chat_request = self._controller.poll_chat()
            if chat_request:
                role, message = chat_request
                response = self._chat_with_agent_fn(role, message, ctx.prompts, ctx.tool_context)
                self._controller.respond_chat(role, response)

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

        # Hook: Controller gate override
        if self._controller is not None:
            override = self._controller.take_gate_override()
            if override:
                ctx.gate_decision = override

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
