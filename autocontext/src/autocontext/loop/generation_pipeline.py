"""GenerationPipeline — composed stage orchestrator for the generation loop."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from autocontext.consultation.stage import stage_consultation
from autocontext.execution.phased_execution import (
    PhaseBudget,
    PhasedExecutionPlan,
    PhasedExecutionResult,
    PhaseResult,
    split_budget,
)
from autocontext.knowledge.coherence import check_coherence
from autocontext.loop.stage_preflight import stage_preflight
from autocontext.loop.stage_prevalidation import stage_prevalidation
from autocontext.loop.stage_probe import stage_probe
from autocontext.loop.stage_staged_validation import stage_staged_validation
from autocontext.loop.stage_tree_search import stage_tree_search
from autocontext.loop.stage_types import GenerationContext
from autocontext.loop.stages import (
    _build_empty_tournament,
    stage_agent_generation,
    stage_curator_gate,
    stage_knowledge_setup,
    stage_persistence,
    stage_policy_refinement,
    stage_skeptic_review,
    stage_stagnation_check,
    stage_tournament,
)
from autocontext.loop.startup_verification import verify_startup

if TYPE_CHECKING:
    from autocontext.agents.curator import KnowledgeCurator
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.backpressure import BackpressureGate
    from autocontext.backpressure.trend_gate import TrendAwareGate
    from autocontext.execution.supervisor import ExecutionSupervisor
    from autocontext.harness.core.controller import LoopController
    from autocontext.harness.meta_optimizer import MetaOptimizer
    from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.storage import ArtifactStore, SQLiteStore

LOGGER = logging.getLogger(__name__)
_PHASE_SCAFFOLDING = "scaffolding"
_PHASE_EXECUTION = "execution"


def _time_remaining(ctx: GenerationContext) -> float | None:
    """Return seconds remaining in the time budget, or None if unlimited."""
    budget = ctx.settings.generation_time_budget_seconds
    if budget <= 0:
        return None
    elapsed = time.monotonic() - ctx.generation_start_time
    return max(0.0, budget - elapsed)


def _over_budget(ctx: GenerationContext) -> bool:
    """True if the generation has exceeded its time budget."""
    remaining = _time_remaining(ctx)
    return remaining is not None and remaining <= 0


def _rollback_for_budget(
    ctx: GenerationContext,
    events: EventStreamEmitter,
    *,
    phase_name: str | None = None,
    phase_budget_seconds: float | None = None,
) -> GenerationContext:
    """Stop the generation before tournament work once the budget is exhausted."""
    ctx.tournament = _build_empty_tournament(ctx)
    ctx.gate_decision = "rollback"
    ctx.gate_delta = 0.0
    ctx.score_history.append(0.0)
    ctx.gate_decision_history.append("rollback")
    events.emit("generation_budget_exhausted", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "budget_seconds": ctx.settings.generation_time_budget_seconds,
        "phase_name": phase_name,
        "phase_budget_seconds": phase_budget_seconds,
    })
    events.emit("gate_decided", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "decision": "rollback",
        "delta": 0.0,
        "tier": "budget",
    })
    return ctx


def _build_phase_plan(ctx: GenerationContext) -> PhasedExecutionPlan | None:
    budget = ctx.settings.generation_time_budget_seconds
    if budget <= 0:
        return None

    scaffolding_ratio = ctx.settings.generation_scaffolding_budget_ratio
    execution_ratio = max(0.0, 1.0 - scaffolding_ratio)
    return split_budget(
        total_seconds=budget,
        phase_names=[_PHASE_SCAFFOLDING, _PHASE_EXECUTION],
        ratios=[scaffolding_ratio, execution_ratio],
        allow_rollover=ctx.settings.generation_phase_budget_rollover_enabled,
    )


def _phase_elapsed_seconds(start_time: float) -> float:
    return max(0.0, time.monotonic() - start_time)


def _phase_exhausted(start_time: float, budget: PhaseBudget | None) -> bool:
    if budget is None:
        return False
    return _phase_elapsed_seconds(start_time) >= budget.budget_seconds


def _build_phase_result(
    *,
    budget: PhaseBudget,
    phase_start_time: float,
    status: str,
    error: str | None = None,
    outputs: dict[str, object] | None = None,
) -> PhaseResult:
    elapsed = _phase_elapsed_seconds(phase_start_time)
    remaining = max(0.0, budget.budget_seconds - elapsed)
    return PhaseResult(
        phase_name=budget.phase_name,
        status=status,
        duration_seconds=round(elapsed, 3),
        budget_seconds=round(budget.budget_seconds, 3),
        budget_remaining_seconds=round(remaining, 3),
        error=error,
        outputs=outputs or {},
    )


def _record_phase_result(
    ctx: GenerationContext,
    events: EventStreamEmitter,
    result: PhaseResult,
    phase_results: list[PhaseResult],
) -> None:
    phase_results.append(result)
    events.emit("generation_phase_result", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        **result.to_dict(),
    })


def _finalize_phased_execution(
    ctx: GenerationContext,
    phase_results: list[PhaseResult],
    plan: PhasedExecutionPlan | None,
) -> None:
    if not phase_results:
        return

    phased_execution = PhasedExecutionResult(
        phase_results=phase_results,
        total_duration_seconds=round(sum(r.duration_seconds for r in phase_results), 3),
        metadata={
            "allow_rollover": plan.allow_rollover if plan is not None else False,
            "phase_count": len(phase_results),
        },
    )
    ctx.phased_execution = phased_execution.to_dict()


def _scaffolding_phase_outputs(ctx: GenerationContext) -> dict[str, object]:
    return {
        "outputs_ready": ctx.outputs is not None,
        "tool_count": len(ctx.created_tools),
        "probe_refinement_applied": ctx.probe_refinement_applied,
        "staged_validation_checks": len(ctx.staged_validation_results or []),
        "strategy_interface_ready": bool(ctx.strategy_interface),
    }


def _execution_phase_outputs(ctx: GenerationContext) -> dict[str, object]:
    matches = 0
    best_score = 0.0
    if ctx.tournament is not None:
        matches = len(ctx.tournament.results)
        best_score = ctx.tournament.best_score
    return {
        "gate_decision": ctx.gate_decision,
        "attempt": ctx.attempt,
        "matches": matches,
        "best_score": best_score,
    }


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
        ctx.generation_start_time = time.monotonic()
        phase_plan = _build_phase_plan(ctx)
        phase_results: list[PhaseResult] = []
        if phase_plan is not None:
            self._events.emit("generation_phase_plan", {
                "run_id": ctx.run_id,
                "generation": ctx.generation,
                "total_budget_seconds": phase_plan.total_budget_seconds,
                "allow_rollover": phase_plan.allow_rollover,
                "phases": [
                    {
                        "phase_name": phase.phase_name,
                        "budget_seconds": phase.budget_seconds,
                    }
                    for phase in phase_plan.phases
                ],
            })

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

        # Stage 0.5: Pre-flight harness synthesis (generation 1 only)
        if ctx.generation == 1:
            ctx = stage_preflight(
                ctx,
                events=self._events,
                artifacts=self._artifacts,
            )

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

        # Stage 2+3: Tree search mode OR standard agent generation + tournament
        use_tree_search = ctx.settings.exploration_mode == "tree"

        if use_tree_search:
            # Tree search combines agent generation + tournament into one stage
            ctx = stage_tree_search(
                ctx,
                orchestrator=self._orchestrator,
                supervisor=self._supervisor,
                artifacts=self._artifacts,
                sqlite=self._sqlite,
                events=self._events,
                on_role_event=_on_role_event,
            )
        else:
            scaffolding_budget = phase_plan.phases[0] if phase_plan is not None else None
            execution_budget_template = phase_plan.phases[1] if phase_plan is not None else None
            scaffolding_started_at = ctx.generation_start_time

            # Standard flow: agent generation → pre-validation → probe → tournament
            try:
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

                # Stage 2.3: Staged validation (progressive checks before tournament)
                if not _over_budget(ctx) and not _phase_exhausted(scaffolding_started_at, scaffolding_budget):
                    ctx = stage_staged_validation(
                        ctx,
                        events=self._events,
                        sqlite=self._sqlite,
                    )

                # Stage 2.4: Pre-validation (optional — dry-run self-play before tournament)
                if not _over_budget(ctx) and not _phase_exhausted(scaffolding_started_at, scaffolding_budget):
                    harness_loader = None
                    if ctx.settings.harness_validators_enabled:
                        from autocontext.execution.harness_loader import HarnessLoader

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
                        supervisor=self._supervisor,
                    )

                # Stage 2.5: Probe (optional — refine strategy from observation)
                if not _over_budget(ctx) and not _phase_exhausted(scaffolding_started_at, scaffolding_budget):
                    ctx = stage_probe(
                        ctx,
                        agents=self._orchestrator,
                        events=self._events,
                        supervisor=self._supervisor,
                    )

                # Stage 2.6: Policy refinement (optional — refine code strategies via zero-LLM evaluation)
                if not _over_budget(ctx) and not _phase_exhausted(scaffolding_started_at, scaffolding_budget):
                    refinement_client, refinement_model = self._orchestrator.resolve_role_execution(
                        "competitor",
                        generation=ctx.generation,
                        scenario_name=ctx.scenario_name,
                    )
                    ctx = stage_policy_refinement(
                        ctx,
                        client=refinement_client,
                        model=refinement_model,
                        events=self._events,
                        sqlite=self._sqlite,
                    )
            except Exception as exc:
                if scaffolding_budget is not None:
                    failed_scaffolding_result = _build_phase_result(
                        budget=scaffolding_budget,
                        phase_start_time=scaffolding_started_at,
                        status="failed",
                        error=str(exc),
                        outputs=_scaffolding_phase_outputs(ctx),
                    )
                    _record_phase_result(ctx, self._events, failed_scaffolding_result, phase_results)
                    if execution_budget_template is not None:
                        skipped_execution = PhaseResult(
                            phase_name=execution_budget_template.phase_name,
                            status="skipped",
                            duration_seconds=0.0,
                            budget_seconds=execution_budget_template.budget_seconds,
                            budget_remaining_seconds=execution_budget_template.budget_seconds,
                            error="Execution phase skipped because scaffolding failed",
                            outputs={},
                        )
                        _record_phase_result(ctx, self._events, skipped_execution, phase_results)
                _finalize_phased_execution(ctx, phase_results, phase_plan)
                raise

            scaffolding_result: PhaseResult | None = None
            execution_budget: PhaseBudget | None = None
            if scaffolding_budget is not None:
                scaffolding_exhausted = _phase_exhausted(scaffolding_started_at, scaffolding_budget)
                scaffolding_status = "timeout" if scaffolding_exhausted else "completed"
                scaffolding_error = None
                if scaffolding_exhausted:
                    scaffolding_error = (
                        f"{_PHASE_SCAFFOLDING} phase exceeded "
                        f"{scaffolding_budget.budget_seconds}s budget before execution"
                    )
                scaffolding_result = _build_phase_result(
                    budget=scaffolding_budget,
                    phase_start_time=scaffolding_started_at,
                    status=scaffolding_status,
                    error=scaffolding_error,
                    outputs=_scaffolding_phase_outputs(ctx),
                )
                _record_phase_result(ctx, self._events, scaffolding_result, phase_results)

                if execution_budget_template is not None:
                    execution_budget_seconds = execution_budget_template.budget_seconds
                    if phase_plan is not None and phase_plan.allow_rollover:
                        execution_budget_seconds += scaffolding_result.budget_remaining_seconds
                    execution_budget = PhaseBudget(
                        phase_name=execution_budget_template.phase_name,
                        budget_seconds=round(execution_budget_seconds, 3),
                    )

            # Stage 3: Tournament + gate
            if scaffolding_result is not None and scaffolding_result.status != "completed":
                if execution_budget is not None:
                    skipped_execution = PhaseResult(
                        phase_name=execution_budget.phase_name,
                        status="skipped",
                        duration_seconds=0.0,
                        budget_seconds=execution_budget.budget_seconds,
                        budget_remaining_seconds=execution_budget.budget_seconds,
                        error="Execution phase skipped because scaffolding exceeded its budget",
                        outputs={},
                    )
                    _record_phase_result(ctx, self._events, skipped_execution, phase_results)
                ctx = _rollback_for_budget(
                    ctx,
                    self._events,
                    phase_name=_PHASE_SCAFFOLDING,
                    phase_budget_seconds=scaffolding_budget.budget_seconds if scaffolding_budget else None,
                )
            elif _over_budget(ctx):
                if execution_budget is not None:
                    execution_result = PhaseResult(
                        phase_name=execution_budget.phase_name,
                        status="skipped",
                        duration_seconds=0.0,
                        budget_seconds=execution_budget.budget_seconds,
                        budget_remaining_seconds=execution_budget.budget_seconds,
                        error="Execution phase skipped because the generation exhausted its overall budget",
                        outputs={},
                    )
                    _record_phase_result(ctx, self._events, execution_result, phase_results)
                ctx = _rollback_for_budget(
                    ctx,
                    self._events,
                    phase_name=_PHASE_EXECUTION if execution_budget is not None else None,
                    phase_budget_seconds=execution_budget.budget_seconds if execution_budget is not None else None,
                )
            elif execution_budget is not None and execution_budget.budget_seconds <= 0:
                execution_result = PhaseResult(
                    phase_name=execution_budget.phase_name,
                    status="skipped",
                    duration_seconds=0.0,
                    budget_seconds=execution_budget.budget_seconds,
                    budget_remaining_seconds=0.0,
                    error="Execution phase has no budget remaining after scaffolding",
                    outputs={},
                )
                _record_phase_result(ctx, self._events, execution_result, phase_results)
                ctx = _rollback_for_budget(
                    ctx,
                    self._events,
                    phase_name=_PHASE_EXECUTION,
                    phase_budget_seconds=execution_budget.budget_seconds,
                )
            else:
                execution_started_at = time.monotonic()
                execution_phase_budget = execution_budget
                try:
                    ctx = stage_tournament(
                        ctx,
                        supervisor=self._supervisor,
                        gate=self._gate,
                        events=self._events,
                        sqlite=self._sqlite,
                        artifacts=self._artifacts,
                        agents=self._orchestrator,
                    )
                except Exception as exc:
                    if execution_phase_budget is not None:
                        execution_result = _build_phase_result(
                            budget=execution_phase_budget,
                            phase_start_time=execution_started_at,
                            status="failed",
                            error=str(exc),
                            outputs={},
                        )
                        _record_phase_result(ctx, self._events, execution_result, phase_results)
                        _finalize_phased_execution(ctx, phase_results, phase_plan)
                    raise

                if execution_phase_budget is not None:
                    execution_result = _build_phase_result(
                        budget=execution_phase_budget,
                        phase_start_time=execution_started_at,
                        status="completed",
                        outputs=_execution_phase_outputs(ctx),
                    )
                    _record_phase_result(ctx, self._events, execution_result, phase_results)

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

        # Stage 3c: Optional provider consultation after stalls/uncertainty
        ctx = stage_consultation(
            ctx,
            sqlite=self._sqlite,
            artifacts=self._artifacts,
            events=self._events,
        )

        # Stage 3.5: Skeptic adversarial review (AC-324)
        ctx = stage_skeptic_review(
            ctx,
            skeptic=self._orchestrator.skeptic,
            artifacts=self._artifacts,
            trajectory_builder=self._trajectory_builder,
            sqlite=self._sqlite,
            events=self._events,
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
            agents=self._orchestrator,
        )

        # Stage 6: Knowledge coherence verification (optional, skipped under time pressure)
        if ctx.settings.coherence_check_enabled and not _over_budget(ctx):
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

        _finalize_phased_execution(ctx, phase_results, phase_plan)

        # Record generation timing (AC-174)
        ctx.generation_elapsed_seconds = time.monotonic() - ctx.generation_start_time
        self._sqlite.update_generation_duration(
            ctx.run_id,
            ctx.generation,
            ctx.generation_elapsed_seconds,
        )
        self._events.emit("generation_timing", {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "elapsed_seconds": round(ctx.generation_elapsed_seconds, 2),
            "budget_seconds": ctx.settings.generation_time_budget_seconds,
            "over_budget": _over_budget(ctx),
            "phased_execution": ctx.phased_execution,
        })
        return ctx
