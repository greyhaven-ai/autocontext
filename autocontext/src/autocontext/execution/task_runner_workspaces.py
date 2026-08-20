"""Production workspace selection and evaluation for queued agent tasks."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from autocontext.config.settings import AppSettings
from autocontext.execution.agent_task_evolution import (
    AgentTaskGenerationEvaluation,
    EvolutionWorkspace,
)
from autocontext.execution.docker_research_sandbox import DockerResearchSandboxBackend
from autocontext.execution.improvement_loop import ImprovementResult, RoundResult
from autocontext.execution.interpreter_workspace import InterpreterWorkspace
from autocontext.execution.research_workspace import ResearchWorkspace
from autocontext.execution.research_workspace_models import (
    WorkspaceCapability,
    WorkspaceCapabilityRequest,
    WorkspaceResourceLimits,
)
from autocontext.scenarios.agent_task import AgentTaskResult


def workspace_factory_from_settings(
    settings: AppSettings | None,
) -> Callable[[], EvolutionWorkspace] | None:
    """Build the explicitly selected workspace backend for queued tasks."""

    if settings is None or not settings.workspace_interpreter_enabled:
        return None
    timeout = settings.workspace_interpreter_timeout_seconds
    if settings.workspace_interpreter_backend == "interpreter":
        if settings.workspace_interpreter_execute_candidates:
            raise ValueError("queued candidate execution requires the docker workspace backend")
        return lambda: InterpreterWorkspace(timeout_seconds=timeout)
    if not settings.workspace_interpreter_capabilities_approved:
        raise ValueError("docker workspace capabilities require explicit operator approval")

    capabilities: set[WorkspaceCapability] = {"workspace_read", "workspace_write"}
    if settings.workspace_interpreter_allowed_imports:
        capabilities.add("package_import")
    if settings.workspace_interpreter_allowed_commands:
        capabilities.add("subprocess")

    def isolated_workspace() -> ResearchWorkspace:
        backend = DockerResearchSandboxBackend(
            image=settings.workspace_interpreter_docker_image,
            memory_mb=settings.workspace_interpreter_memory_mb,
            cpu_count=settings.workspace_interpreter_cpu_count,
            pids_limit=settings.workspace_interpreter_pids_limit,
        )
        request = WorkspaceCapabilityRequest(
            workspace_id=f"task-runner-{uuid.uuid4().hex}",
            profile="isolated_sandbox",
            requested_capabilities=frozenset(capabilities),
            allowed_imports=frozenset(settings.workspace_interpreter_allowed_imports),
            allowed_commands=frozenset(settings.workspace_interpreter_allowed_commands),
            limits=WorkspaceResourceLimits(timeout_seconds=timeout),
            lifecycle="delete_on_close",
            approval_context={"source": "task-runner-settings"},
        )
        return ResearchWorkspace(request, approver=lambda _: True, sandbox_backend=backend)

    return isolated_workspace


def evaluate_workspace_candidate(
    program: str,
    workspace: EvolutionWorkspace,
    *,
    evaluate_output: Callable[..., AgentTaskResult],
    quality_threshold: float,
    reference_context: str | None,
    required_concepts: Sequence[str] | None,
    calibration_examples: Sequence[dict[str, Any]] | None,
) -> tuple[AgentTaskGenerationEvaluation, ImprovementResult]:
    """Execute a candidate and judge only its observable sandbox result."""

    run = getattr(workspace, "run", None)
    if not callable(run):
        raise TypeError("selected capable workspace does not expose code execution")
    execution = run(program)
    rendered = str(execution.stdout).strip()
    if execution.answer:
        answer_json = json.dumps(execution.answer, sort_keys=True)
        rendered = f"{rendered}\n{answer_json}".strip()
    if execution.error:
        judge_result = AgentTaskResult(
            score=0.0,
            reasoning=f"Workspace execution failed: {execution.error}",
            dimension_scores={"execution": 0.0},
        )
    else:
        judge_result = evaluate_output(
            rendered,
            {},
            reference_context=reference_context,
            required_concepts=list(required_concepts) if required_concepts is not None else None,
            calibration_examples=(list(calibration_examples) if calibration_examples is not None else None),
        )
    round_result = RoundResult(
        round_number=1,
        output=program,
        score=judge_result.score,
        reasoning=judge_result.reasoning,
        dimension_scores=judge_result.dimension_scores,
        judge_failed=bool(execution.error),
        evaluator_epoch=judge_result.evaluator_epoch,
    )
    met_threshold = execution.error is None and judge_result.score >= quality_threshold
    improvement = ImprovementResult(
        rounds=[round_result],
        best_output=program,
        best_score=judge_result.score,
        best_round=1,
        total_rounds=1,
        met_threshold=met_threshold,
        judge_failures=int(bool(execution.error)),
        termination_reason="threshold_met" if met_threshold else "max_rounds",
        judge_calls=int(execution.error is None),
        evaluator_epoch=judge_result.evaluator_epoch,
        metadata={
            "workspace_execution": {
                "error": execution.error,
                "observed_output": rendered,
                "stdout_chars": len(execution.stdout),
                "answer_keys": sorted(execution.answer),
            }
        },
    )
    evaluation = AgentTaskGenerationEvaluation(
        output=program,
        score=judge_result.score,
        reasoning=judge_result.reasoning,
        dimension_scores=judge_result.dimension_scores,
        met_threshold=met_threshold,
        metadata={"workspace_error": execution.error},
    )
    return evaluation, improvement


__all__ = ["evaluate_workspace_candidate", "workspace_factory_from_settings"]
