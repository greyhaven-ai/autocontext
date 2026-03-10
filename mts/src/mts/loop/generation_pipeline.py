"""GenerationPipeline — composed stage orchestrator for the generation loop."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from mts.knowledge.coherence import check_coherence
from mts.loop.stage_prevalidation import stage_prevalidation
from mts.loop.stage_probe import stage_probe
from mts.loop.stage_types import GenerationContext
from mts.loop.stages import (
    stage_agent_generation,
    stage_curator_gate,
    stage_knowledge_setup,
    stage_persistence,
    stage_stagnation_check,
    stage_tournament,
)
from mts.loop.startup_verification import verify_startup

if TYPE_CHECKING:
    from mts.agents.curator import KnowledgeCurator
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.backpressure import BackpressureGate
    from mts.backpressure.trend_gate import TrendAwareGate
    from mts.execution.supervisor import ExecutionSupervisor
    from mts.harness.core.controller import LoopController
    from mts.harness.meta_optimizer import MetaOptimizer
    from mts.knowledge.trajectory import ScoreTrajectoryBuilder
    from mts.loop.events import EventStreamEmitter
    from mts.storage import ArtifactStore, SQLiteStore

LOGGER = logging.getLogger(__name__)


class GenerationPipeline:
    """Orchestrates a single generation through decomposed stages."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        supervisor: ExecutionSupervisor,
        gate: BackpressureGate | TrendAwareGate,
        artifacts: ArtifactStore,
        sqlite: SQLiteStore,
        trajectory_builder: ScoreTrajectoryBuilder,
        events: EventStreamEmitter,
        curator: KnowledgeCurator | None,
        controller: LoopController | None = None,
        warm_provision_fn: Callable[..., dict] | None = None,
        chat_with_agent_fn: Callable[[str, str, object, str], str] | None = None,
        meta_optimizer: MetaOptimizer | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._supervisor = supervisor
        self._gate = gate
        self._artifacts = artifacts
        self._sqlite = sqlite
        self._trajectory_builder = trajectory_builder
        self._events = events
        self._curator = curator
        self._controller = controller
        self._warm_provision_fn = warm_provision_fn
        self._chat_with_agent_fn = chat_with_agent_fn
        self._meta_optimizer = meta_optimizer

    def run_generation(self, ctx: GenerationContext) -> GenerationContext:
        """Execute all stages for a single generation."""

        def _on_role_event(role: str, status: str) -> None:
            self._events.emit("role_event", {
                "run_id": ctx.run_id, "generation": ctx.generation,
                "role": role, "status": status,
            })

        # Stage 0: Startup verification (generation 1 only)
        if ctx.generation == 1:
            report = verify_startup(
                scenario_name=ctx.scenario_name,
                knowledge_root=self._artifacts.knowledge_root,
                db_path=ctx.settings.db_path,
            )
            if report.warnings:
                self._events.emit("startup_verification", {
                    "run_id": ctx.run_id,
                    "warnings": report.warnings,
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

        # Meta-optimization: record LLM calls
        if self._meta_optimizer is not None and ctx.outputs is not None:
            try:
                for role_exec in ctx.outputs.role_executions:
                    self._meta_optimizer.record_llm_call(role_exec.role, role_exec.usage, ctx.generation)
            except Exception:
                LOGGER.debug("meta_optimizer.record_llm_call failed", exc_info=True)

        # Hook: Controller chat checkpoint
        if self._controller is not None and self._chat_with_agent_fn is not None:
            chat_request = self._controller.poll_chat()
            if chat_request:
                role, message = chat_request
                response = self._chat_with_agent_fn(role, message, ctx.prompts, ctx.tool_context)
                self._controller.respond_chat(role, response)

        # Stage 2.4: Pre-validation (optional — dry-run self-play before tournament)
        harness_loader = None
        if ctx.settings.harness_validators_enabled:
            from mts.execution.harness_loader import HarnessLoader

            h_dir = self._artifacts.harness_dir(ctx.scenario_name)
            if h_dir.exists():
                harness_loader = HarnessLoader(h_dir, timeout_seconds=ctx.settings.harness_timeout_seconds)
                harness_loader.load()

        ctx = stage_prevalidation(
            ctx,
            events=self._events,
            agents=self._orchestrator,
            harness_loader=harness_loader,
            artifacts=self._artifacts,
        )

        # Stage 2.5: Probe (optional — refine strategy from observation)
        ctx = stage_probe(
            ctx,
            agents=self._orchestrator,
            events=self._events,
            supervisor=self._supervisor,
        )

        # Stage 3: Tournament + gate
        ctx = stage_tournament(
            ctx,
            supervisor=self._supervisor,
            gate=self._gate,
            events=self._events,
            sqlite=self._sqlite,
            artifacts=self._artifacts,
            agents=self._orchestrator,
        )

        # Stage 3b: Stagnation check
        ctx = stage_stagnation_check(
            ctx,
            artifacts=self._artifacts,
            events=self._events,
        )

        # Hook: Controller gate override
        if self._controller is not None:
            override = self._controller.take_gate_override()
            if override:
                ctx.gate_decision = override

        # Meta-optimization: record gate decision
        if self._meta_optimizer is not None:
            try:
                self._meta_optimizer.record_gate_decision(
                    ctx.gate_decision, ctx.gate_delta, ctx.generation,
                )
            except Exception:
                LOGGER.debug("meta_optimizer.record_gate_decision failed", exc_info=True)

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

        # Stage 6: Knowledge coherence verification (optional)
        if ctx.settings.coherence_check_enabled:
            coherence = check_coherence(
                scenario_name=ctx.scenario_name,
                knowledge_root=self._artifacts.knowledge_root,
                skills_root=self._artifacts.skills_root,
            )
            if coherence.issues:
                self._events.emit("coherence_warning", {
                    "run_id": ctx.run_id,
                    "generation": ctx.generation,
                    "issues": coherence.issues,
                })

        # Meta-optimization: record full generation metrics
        if self._meta_optimizer is not None and ctx.outputs is not None:
            try:
                role_usages = {role_exec.role: role_exec.usage for role_exec in ctx.outputs.role_executions}
                self._meta_optimizer.record_generation(
                    generation=ctx.generation,
                    role_usages=role_usages,
                    gate_decision=ctx.gate_decision,
                    score_delta=ctx.gate_delta,
                )
            except Exception:
                LOGGER.debug("meta_optimizer.record_generation failed", exc_info=True)

        return ctx
