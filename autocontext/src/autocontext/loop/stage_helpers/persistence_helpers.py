"""Stage helpers — persistence_helpers (extracted from stages.py, AC-482)."""

from __future__ import annotations

import json
import logging
from math import isfinite
from typing import TYPE_CHECKING, Any

from autocontext.agents.feedback_loops import AnalystRating
from autocontext.analytics.credit_assignment import (
    CreditAssignmentRecord,
    attribute_credit,
    build_span_attribution,
    compute_change_vector,
)
from autocontext.analytics.exploration_collapse_guard import (
    ExplorationSnapshot,
    GuidanceChange,
    detect_exploration_collapse,
)
from autocontext.knowledge.dead_end_manager import DeadEndEntry, consolidate_dead_ends
from autocontext.knowledge.progress import build_progress_snapshot
from autocontext.loop.stage_helpers.context_loaders import _current_tool_names
from autocontext.loop.stage_types import GenerationContext

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.agents.curator import KnowledgeCurator
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder
    from autocontext.storage import ArtifactStore, SQLiteStore


def _revise_strategy_for_validity_failure(
    ctx: GenerationContext,
    *,
    current_strategy: dict[str, Any],
    errors: list[str],
    retry_attempt: int,
    agents: AgentOrchestrator | None,
) -> dict[str, Any] | None:
    """Ask the competitor to fix an invalid strategy before running matches."""
    if agents is None or ctx.prompts is None:
        return None

    is_code_strategy = "__code__" in current_strategy
    retry_prompt = (
        ctx.prompts.competitor
        + f"\n\n--- VALIDITY RETRY ATTEMPT {retry_attempt} ---\n"
        + "Your previous strategy failed pre-tournament validation.\n"
        + "Validation errors:\n"
        + "\n".join(f"- {error}" for error in errors)
        + "\n"
    )
    if is_code_strategy:
        retry_prompt += "Adjust your code so it satisfies the harness and scenario contracts.\n"
        if ctx.settings.code_strategies_enabled:
            from autocontext.prompts.templates import code_strategy_competitor_suffix

            retry_prompt += code_strategy_competitor_suffix(ctx.strategy_interface)
    else:
        retry_prompt += (
            f"Previous strategy: {json.dumps(current_strategy, sort_keys=True)}\n"
            "Return a revised valid strategy. Do not repeat the same invalid approach.\n"
        )

    try:
        raw_text, _ = agents.competitor.run(retry_prompt, tool_context=ctx.tool_context)
        if is_code_strategy:
            revised_strategy, _ = agents.translator.translate_code(raw_text)
        else:
            revised_strategy, _ = agents.translator.translate(raw_text, ctx.strategy_interface)
        return revised_strategy
    except Exception:
        logger.debug("validity retry competitor re-invocation failed", exc_info=True)
        return None


def _apply_tuning_to_settings(
    ctx: GenerationContext,
    parameters: dict[str, float | int],
) -> None:
    """Apply validated tuning parameters to ctx.settings (Pydantic model copy)."""
    if not parameters:
        return
    update: dict[str, Any] = {}
    for key, value in parameters.items():
        if hasattr(ctx.settings, key):
            update[key] = value
    if update:
        ctx.settings = ctx.settings.model_copy(update=update)


def _build_credit_assignment_record(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> CreditAssignmentRecord | None:
    """Compute a durable attribution record from the persisted generation state."""
    outputs = ctx.outputs
    if outputs is None:
        return None

    score_delta = ctx.gate_delta
    previous_state = {
        "playbook": ctx.base_playbook,
        "tools": ctx.base_tool_names,
        "hints": ctx.applied_competitor_hints,
        "analysis": ctx.base_analysis,
    }
    current_state = {
        "playbook": outputs.coach_playbook if ctx.gate_decision == "advance" else ctx.base_playbook,
        "tools": _current_tool_names(ctx, artifacts=artifacts),
        "hints": ctx.coach_competitor_hints,
        "analysis": outputs.analysis_markdown,
    }
    vector = compute_change_vector(
        generation=ctx.generation,
        score_delta=score_delta,
        previous_state=previous_state,
        current_state=current_state,
    )
    attribution = attribute_credit(vector)
    metadata = {
        "gate_decision": ctx.gate_decision,
        "scenario_name": ctx.scenario_name,
        "context_attribution": ctx.settings.context_attribution,
    }
    if ctx.settings.context_attribution == "span":
        attribution.metadata["context_attribution"] = "span"
        attribution.metadata["span_attribution"] = build_span_attribution(
            vector,
            attribution,
            current_state=current_state,
        )
    return CreditAssignmentRecord(
        run_id=ctx.run_id,
        generation=ctx.generation,
        vector=vector,
        attribution=attribution,
        metadata=metadata,
    )


def _maybe_rate_analyst_output(
    ctx: GenerationContext,
    *,
    curator: KnowledgeCurator | None,
    artifacts: ArtifactStore,
    sqlite: SQLiteStore,
) -> AnalystRating | None:
    """Persist curator feedback on analyst quality when there is a real report to rate."""
    if curator is None or ctx.settings.ablation_no_feedback:
        return None
    outputs = ctx.outputs
    if outputs is None:
        return None
    analysis_markdown = getattr(outputs, "analysis_markdown", "")
    if not isinstance(analysis_markdown, str) or not analysis_markdown.strip():
        return None

    tournament = ctx.tournament
    score_summary = ""
    if tournament is not None:
        score_summary = (
            f"Generation {ctx.generation}: best_score={tournament.best_score:.4f}, "
            f"mean_score={tournament.mean_score:.4f}, gate_decision={ctx.gate_decision or 'pending'}"
        )
    rating, exec_result = curator.rate_analyst_output(
        analysis_markdown,
        generation=ctx.generation,
        score_summary=score_summary,
        constraint_mode=ctx.settings.constraint_prompts_enabled,
    )
    artifacts.write_analyst_rating(ctx.scenario_name, ctx.generation, rating)
    sqlite.append_generation_agent_activity(
        ctx.run_id,
        ctx.generation,
        outputs=[
            ("curator_analyst_rating", json.dumps(rating.to_dict(), sort_keys=True)),
            ("curator_analyst_feedback", exec_result.content),
        ],
        role_metrics=[(
            exec_result.role,
            exec_result.usage.model,
            exec_result.usage.input_tokens,
            exec_result.usage.output_tokens,
            exec_result.usage.latency_ms,
            exec_result.subagent_id,
            exec_result.status,
        )],
    )
    return rating


def _persist_skill_note(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
    playbook_result: str = "live",
) -> None:
    """Write skill note — advance lessons or rollback warning."""
    tournament = ctx.tournament
    assert tournament is not None  # caller guarantees
    outputs = ctx.outputs
    assert outputs is not None
    gate_decision = ctx.gate_decision
    gate_delta = ctx.gate_delta
    generation = ctx.generation
    settings = ctx.settings

    if gate_decision == "advance":
        if ctx.require_playbook_approval:
            return
        skill_lessons = outputs.coach_lessons
    else:
        retry_note = f" after {ctx.attempt} retries" if ctx.attempt > 0 else ""
        skill_lessons = (
            f"- Generation {generation} ROLLBACK{retry_note} "
            f"(score={tournament.best_score:.4f}, "
            f"delta={gate_delta:+.4f}, threshold={settings.backpressure_min_delta}). "
            f"Strategy: {json.dumps(ctx.current_strategy, sort_keys=True)[:200]}. "
            f"Narrative: {ctx.replay_narrative[:150]}. "
            f"Avoid this approach."
        )
    artifacts.persist_skill_note(
        scenario_name=ctx.scenario_name,
        generation_index=generation,
        decision=gate_decision,
        lessons=skill_lessons,
    )
    _maybe_persist_exploration_collapse_report(ctx, artifacts=artifacts)

    # Dead-end registry: record rollback as dead end
    if gate_decision == "rollback" and settings.dead_end_tracking_enabled:
        strategy_json = json.dumps(ctx.current_strategy, sort_keys=True)
        entry = DeadEndEntry.from_rollback(
            generation=generation,
            strategy=strategy_json,
            score=tournament.best_score,
        )
        artifacts.append_dead_end(ctx.scenario_name, entry.to_markdown())


def _maybe_persist_exploration_collapse_report(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> None:
    settings = ctx.settings
    if not settings.exploration_collapse_guard or settings.ablation_no_feedback:
        return
    changes = _exploration_guidance_changes(ctx)
    snapshots = _exploration_snapshots(ctx)
    if not changes or len(snapshots) < 2:
        return
    try:
        report = detect_exploration_collapse(
            snapshots,
            changes,
            advisory_only=not settings.exploration_collapse_auto_mitigation,
            auto_mitigation=settings.exploration_collapse_auto_mitigation,
        )
        if report.events:
            artifacts.write_json(
                artifacts.generation_dir(ctx.run_id, ctx.generation) / "exploration_collapse_guard.json",
                {"schema_version": report.schema_version, "advisory_only": report.advisory_only, "events": report.records},
            )
    except Exception:
        logger.warning("failed to persist exploration collapse guard report", exc_info=True)


def _exploration_guidance_changes(ctx: GenerationContext) -> list[GuidanceChange]:
    changes: list[GuidanceChange] = []
    if ctx.applied_competitor_hints.strip():
        changes.append(
            GuidanceChange(
                change_id=f"competitor-hints-gen-{ctx.generation}",
                generation_index=ctx.generation,
                kind="hint",
                source_component="competitor_hints",
                source_span="hints.md",
            )
        )
    if ctx.base_playbook.strip() and "No playbook yet" not in ctx.base_playbook:
        changes.append(
            GuidanceChange(
                change_id=f"playbook-gen-{ctx.generation}",
                generation_index=ctx.generation,
                kind="playbook_update",
                source_component="playbook",
                source_span="playbook.md",
            )
        )
    return changes


def _exploration_snapshots(ctx: GenerationContext) -> list[ExplorationSnapshot]:
    scores = [float(score) for score in ctx.score_history if isinstance(score, int | float) and isfinite(float(score))]
    start = ctx.generation - len(scores) + 1
    snapshots: list[ExplorationSnapshot] = []
    for index, score in enumerate(scores):
        gate = ctx.gate_decision_history[index] if index < len(ctx.gate_decision_history) else ""
        snapshots.append(
            ExplorationSnapshot(
                generation_index=max(0, start + index),
                response_length=1.0,
                route_signature=gate or None,
                rollback_rate=1.0 if gate == "rollback" else 0.0,
                score=score,
            )
        )
    return snapshots


def _run_curator_consolidation(
    ctx: GenerationContext,
    *,
    curator: KnowledgeCurator,
    artifacts: ArtifactStore,
    trajectory_builder: ScoreTrajectoryBuilder,
    sqlite: SQLiteStore,
) -> None:
    """Consolidate lessons and dead-ends via curator."""
    settings = ctx.settings
    scenario_name = ctx.scenario_name

    existing_lessons = artifacts.read_skill_lessons_raw(scenario_name)
    if len(existing_lessons) <= settings.skill_max_lessons:
        return

    consolidation_trajectory = trajectory_builder.build_trajectory(ctx.run_id)
    lesson_result, lesson_exec = curator.consolidate_lessons(
        existing_lessons, settings.skill_max_lessons, consolidation_trajectory,
        constraint_mode=settings.constraint_prompts_enabled,
    )
    artifacts.replace_skill_lessons(scenario_name, lesson_result.consolidated_lessons)
    sqlite.append_generation_agent_activity(
        ctx.run_id,
        ctx.generation,
        outputs=[("curator_consolidation", lesson_exec.content)],
        role_metrics=[(
            lesson_exec.role,
            lesson_exec.usage.model,
            lesson_exec.usage.input_tokens,
            lesson_exec.usage.output_tokens,
            lesson_exec.usage.latency_ms,
            lesson_exec.subagent_id,
            lesson_exec.status,
        )],
    )

    # Dead-end consolidation
    if settings.dead_end_tracking_enabled:
        dead_end_text = artifacts.read_dead_ends(scenario_name)
        if dead_end_text:
            consolidated = consolidate_dead_ends(dead_end_text, max_entries=settings.dead_end_max_entries)
            artifacts.replace_dead_ends(scenario_name, consolidated)


def _persist_progress_snapshot(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> None:
    """Write progress JSON snapshot if enabled."""
    tournament = ctx.tournament
    assert tournament is not None  # caller guarantees
    scenario_name = ctx.scenario_name

    progress_lessons = artifacts.read_skill_lessons_raw(scenario_name)
    snapshot = build_progress_snapshot(
        generation=ctx.generation,
        best_score=ctx.previous_best,
        best_elo=ctx.challenger_elo,
        mean_score=tournament.mean_score,
        gate_history=ctx.gate_decision_history,
        score_history=ctx.score_history,
        current_strategy=ctx.current_strategy,
        lessons=[lesson.lstrip("- ") for lesson in progress_lessons],
        scoring_backend=tournament.scoring_backend,
        rating_uncertainty=ctx.challenger_uncertainty,
    )
    artifacts.write_progress(scenario_name, snapshot.to_dict())
