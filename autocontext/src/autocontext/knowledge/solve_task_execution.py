from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.agents.provider_bridge import configured_role_provider
from autocontext.agents.role_runtime_overrides import settings_for_budgeted_role_call
from autocontext.config.settings import AppSettings
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.extensions import HookBus, HookEvents, active_hook_bus
from autocontext.loop.runner_hooks import (
    emit_generation_end,
    emit_generation_failed,
    emit_run_completed,
    emit_run_failed,
    emit_run_start,
)
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.storage import artifact_store_from_settings
from autocontext.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

RoleRuntimeResolver = Callable[..., tuple[Any, str]]


@dataclass(slots=True)
class SolveExecutionSummary:
    run_id: str
    generations_executed: int
    best_score: float


@dataclass(slots=True)
class _SolveHookSurface:
    settings: AppSettings
    hook_bus: HookBus
    loaded_extensions: list[str]


@dataclass(slots=True)
class _SolveGenerationBudget:
    scenario_name: str
    budget_seconds: int
    started_at: float = field(default_factory=lambda: time.monotonic())

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def remaining_seconds(self) -> float | None:
        if self.budget_seconds <= 0:
            return None
        return max(0.0, float(self.budget_seconds) - self.elapsed_seconds())

    def deadline(self) -> float | None:
        if self.budget_seconds <= 0:
            return None
        return self.started_at + float(self.budget_seconds)

    def check(self, phase: str) -> None:
        if self.budget_seconds <= 0:
            return
        elapsed = self.elapsed_seconds()
        if elapsed >= self.budget_seconds:
            raise TimeoutError(
                f"Solve generation time budget exceeded during {phase} "
                f"after {elapsed:.2f}s for scenario '{self.scenario_name}' "
                f"(budget {self.budget_seconds}s)"
            )


def _settings_for_budgeted_role_call(settings: AppSettings, budget: _SolveGenerationBudget, role: str) -> AppSettings:
    effective_provider = configured_role_provider(role, settings) or settings.agent_provider
    try:
        role_settings, _ = settings_for_budgeted_role_call(
            settings,
            effective_provider,
            role,
            budget.deadline(),
        )
    except TimeoutError as exc:
        raise TimeoutError(
            f"Solve generation time budget exhausted before {role} provider call "
            f"after {budget.elapsed_seconds():.2f}s for scenario '{budget.scenario_name}' "
            f"(budget {budget.budget_seconds}s)"
        ) from exc
    return role_settings


class _BudgetedAgentTask(AgentTaskInterface):
    """Add solve generation budget checks around an AgentTaskInterface."""

    def __init__(self, task: AgentTaskInterface, budget: _SolveGenerationBudget) -> None:
        self._task = task
        self._budget = budget
        self.name = getattr(task, "name", task.__class__.__name__)

    def get_task_prompt(self, state: dict) -> str:
        self._budget.check("task prompt")
        prompt = self._task.get_task_prompt(state)
        self._budget.check("task prompt")
        return prompt

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        self._budget.check("evaluation")
        result = self._task.evaluate_output(
            output,
            state,
            reference_context=reference_context,
            required_concepts=required_concepts,
            calibration_examples=calibration_examples,
            pinned_dimensions=pinned_dimensions,
        )
        self._budget.check("evaluation")
        return result

    def get_rubric(self) -> str:
        self._budget.check("rubric")
        rubric = self._task.get_rubric()
        self._budget.check("rubric")
        return rubric

    def initial_state(self, seed: int | None = None) -> dict:
        self._budget.check("initial state")
        state = self._task.initial_state(seed)
        self._budget.check("initial state")
        return state

    def describe_task(self) -> str:
        self._budget.check("task description")
        description = self._task.describe_task()
        self._budget.check("task description")
        return description

    def prepare_context(self, state: dict) -> dict:
        self._budget.check("context preparation")
        prepared = self._task.prepare_context(state)
        self._budget.check("context preparation")
        return prepared

    def validate_context(self, state: dict) -> list[str]:
        self._budget.check("context validation")
        errors = self._task.validate_context(state)
        self._budget.check("context validation")
        return errors

    def revise_output(
        self,
        output: str,
        judge_result: AgentTaskResult,
        state: dict,
    ) -> str:
        self._budget.check("revision")
        revised = self._task.revise_output(output, judge_result, state)
        self._budget.check("revision")
        return revised

    def verify_facts(self, output: str, state: dict) -> dict | None:
        self._budget.check("fact verification")
        result = self._task.verify_facts(output, state)
        self._budget.check("fact verification")
        return result


def run_task_like_scenario(
    *,
    settings: AppSettings,
    runtime_settings: AppSettings,
    migrations_dir: Path,
    scenario_name: str,
    scenario_type: str,
    task: AgentTaskInterface,
    max_rounds: int,
    hook_bus: HookBus,
    loaded_extensions: list[str],
    role_runtime_resolver: RoleRuntimeResolver,
) -> SolveExecutionSummary:
    sqlite = SQLiteStore(settings.db_path)
    sqlite.migrate(migrations_dir)
    active_run_id = f"solve_{scenario_name}_{uuid.uuid4().hex[:8]}"
    sqlite.create_run(
        active_run_id,
        scenario_name,
        1,
        scenario_type,
        agent_provider=settings.agent_provider,
    )
    sqlite.upsert_generation(
        active_run_id,
        1,
        mean_score=0.0,
        best_score=0.0,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision="running",
        status="running",
    )
    budget = _SolveGenerationBudget(
        scenario_name=scenario_name,
        budget_seconds=settings.generation_time_budget_seconds,
    )
    hook_surface = _SolveHookSurface(settings=settings, hook_bus=hook_bus, loaded_extensions=loaded_extensions)
    generation_started = False

    try:
        emit_run_start(hook_surface, run_id=active_run_id, scenario=scenario_name, target_generations=1)
        generation_start = hook_bus.emit(
            HookEvents.GENERATION_START,
            {
                "run_id": active_run_id,
                "scenario": scenario_name,
                "generation": 1,
            },
        )
        generation_start.raise_if_blocked()
        generation_started = True
        budget.check("runtime resolution")
        role_runtime_settings = _settings_for_budgeted_role_call(runtime_settings, budget, "competitor")
        provider, provider_model = role_runtime_resolver(
            role_runtime_settings,
            role="competitor",
            scenario_name=scenario_name,
            sqlite=sqlite,
            hook_bus=hook_bus,
            generation_deadline=budget.deadline(),
        )
        budget.check("runtime resolution")
        budgeted_task = _BudgetedAgentTask(task, budget)
        state = budgeted_task.prepare_context(budgeted_task.initial_state())
        context_errors = budgeted_task.validate_context(state)
        if context_errors:
            raise ValueError(f"Context validation failed: {'; '.join(context_errors)}")
        prompt = budgeted_task.get_task_prompt(state)
        with active_hook_bus(hook_bus):
            initial_output = provider.complete(
                system_prompt="Complete the task precisely.",
                user_prompt=prompt,
                model=provider_model,
            ).text
            budget.check("initial generation")
            sqlite.append_agent_output(active_run_id, 1, "competitor_initial", initial_output)

            loop = ImprovementLoop(task=budgeted_task, max_rounds=max_rounds)
            result = loop.run(initial_output=initial_output, state=state)
            budget.check("improvement loop")
    except Exception as exc:
        sqlite.upsert_generation(
            active_run_id,
            1,
            mean_score=0.0,
            best_score=0.0,
            elo=0.0,
            wins=0,
            losses=0,
            gate_decision="failed",
            status="failed",
            duration_seconds=budget.elapsed_seconds(),
        )
        sqlite.mark_run_failed(active_run_id)
        if generation_started:
            try:
                emit_generation_failed(
                    hook_surface,
                    run_id=active_run_id,
                    scenario=scenario_name,
                    generation=1,
                    error=str(exc),
                )
            except Exception:
                logger.debug("GENERATION_END hook failed after solve generation failure", exc_info=True)
        try:
            emit_run_failed(
                hook_surface,
                run_id=active_run_id,
                scenario=scenario_name,
                completed_generations=0,
                best_score=0.0,
                elo=0.0,
                error=str(exc),
            )
        except Exception:
            logger.debug("RUN_END hook failed after solve run failure", exc_info=True)
        raise

    sqlite.append_agent_output(active_run_id, 1, "competitor", result.best_output)
    sqlite.upsert_generation(
        active_run_id,
        1,
        mean_score=result.best_score,
        best_score=result.best_score,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision=result.termination_reason,
        status="completed",
        duration_seconds=(result.duration_ms / 1000.0) if result.duration_ms is not None else None,
    )
    sqlite.mark_run_completed(active_run_id)
    if settings.cross_run_inheritance and not settings.ablation_no_feedback:
        artifacts = artifact_store_from_settings(settings)
        playbook_hash = artifacts.snapshot_knowledge(scenario_name, active_run_id)
        sqlite.save_knowledge_snapshot(
            scenario=scenario_name,
            run_id=active_run_id,
            best_score=result.best_score,
            best_elo=1500.0,
            playbook_hash=playbook_hash,
            agent_provider=settings.agent_provider,
            rlm_enabled=settings.rlm_enabled,
            scoring_backend=settings.scoring_backend,
        )
    emit_generation_end(
        hook_surface,
        {
            "run_id": active_run_id,
            "scenario": scenario_name,
            "generation": 1,
            "status": "completed",
            "elapsed_seconds": budget.elapsed_seconds(),
            "gate_decision": result.termination_reason,
            "best_score": result.best_score,
            "elo": 1500.0,
            "phased_execution": False,
        },
    )
    emit_run_completed(
        hook_surface,
        run_id=active_run_id,
        scenario=scenario_name,
        completed_generations=1,
        best_score=result.best_score,
        elo=1500.0,
        session_report_path=None,
        dead_ends_found=0,
        extra={"improvement_rounds": result.total_rounds},
    )
    return SolveExecutionSummary(
        run_id=active_run_id,
        generations_executed=result.total_rounds,
        best_score=result.best_score,
    )
