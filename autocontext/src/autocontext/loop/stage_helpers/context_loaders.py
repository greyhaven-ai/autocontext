"""Stage helpers — context_loaders (extracted from stages.py, AC-482)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from autocontext.agents.feedback_loops import AnalystRating, ToolUsageTracker, format_analyst_feedback
from autocontext.agents.hint_feedback import (
    HintFeedback,
    build_hint_reflection_prompt,
    format_hint_feedback_for_coach,
    parse_hint_feedback,
    prepare_hint_reflection_items,
)
from autocontext.analytics.credit_assignment import (
    CreditAssignmentRecord,
    format_attribution_for_agent,
)
from autocontext.harness.core.types import RoleExecution, RoleUsage
from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy
from autocontext.loop.stage_types import GenerationContext

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.storage import ArtifactStore, SQLiteStore


def _load_validity_harness_loader(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> Any | None:
    """Load harness validators for two-tier validity checks when enabled."""
    if not ctx.settings.harness_validators_enabled:
        return None

    from autocontext.execution.harness_loader import HarnessLoader

    harness_dir = artifacts.harness_dir(ctx.scenario_name)
    if not harness_dir.exists():
        return None

    loader = HarnessLoader(
        harness_dir,
        timeout_seconds=ctx.settings.harness_timeout_seconds,
    )
    loader.load()
    return loader


def _load_analyst_feedback_section(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> str:
    """Read the latest curator rating for injection into the next analyst prompt."""
    raw_rating = artifacts.read_latest_analyst_rating(ctx.scenario_name, ctx.generation)
    if not isinstance(raw_rating, AnalystRating):
        return ""
    return format_analyst_feedback(raw_rating)


def _load_architect_tool_usage_report(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> str:
    """Read the architect-facing report on which tools the competitor actually uses."""
    if ctx.generation <= 1:
        return ""
    report = artifacts.read_tool_usage_report(
        ctx.scenario_name,
        current_generation=ctx.generation - 1,
    )
    return report if isinstance(report, str) else ""


def _load_hint_feedback_section(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> str:
    """Read the latest competitor feedback so the next coach prompt can use it."""
    if ctx.generation <= 1:
        return ""
    raw_feedback = artifacts.read_latest_hint_feedback(ctx.scenario_name, ctx.generation)
    if not isinstance(raw_feedback, HintFeedback):
        return ""
    return format_hint_feedback_for_coach(raw_feedback)


def _load_credit_attribution_section(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
    role: str,
) -> str:
    """Read the latest attribution record and format it for a specific agent role."""
    if ctx.generation <= 1:
        return ""
    raw_record = artifacts.read_latest_credit_assignment(
        ctx.scenario_name,
        run_id=ctx.run_id,
        current_gen=ctx.generation,
    )
    if not isinstance(raw_record, CreditAssignmentRecord):
        return ""
    return format_attribution_for_agent(raw_record.attribution, role)


def _normalize_tool_names(raw: object) -> list[str]:
    """Normalize tool names from persisted lists or created-tool markers."""
    if not isinstance(raw, list):
        return []
    normalized: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value:
            continue
        if value.endswith(" (updated)"):
            value = value[: -len(" (updated)")]
        if value.endswith(".py"):
            value = value[:-3]
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _current_tool_names(ctx: GenerationContext, *, artifacts: ArtifactStore) -> list[str]:
    """Return the persisted post-generation tool set with a safe fallback for tests."""
    if hasattr(artifacts, "list_tool_names"):
        raw_names = artifacts.list_tool_names(ctx.scenario_name)
        normalized = _normalize_tool_names(raw_names)
        if normalized:
            return normalized
    merged = [*ctx.base_tool_names, *_normalize_tool_names(ctx.created_tools)]
    deduped: list[str] = []
    for name in merged:
        if name not in deduped:
            deduped.append(name)
    return deduped


def _update_tool_usage_feedback(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> None:
    """Track competitor tool references so the architect sees real adoption data next gen."""
    outputs = ctx.outputs
    if outputs is None or outputs.competitor_output is None:
        return
    raw_text = outputs.competitor_output.raw_text
    if not isinstance(raw_text, str) or not raw_text.strip():
        return

    known_tools = artifacts.list_tool_names(ctx.scenario_name)
    if not isinstance(known_tools, list):
        return
    tool_names = sorted({name for name in known_tools if isinstance(name, str) and name})
    if not tool_names:
        return

    tracker = artifacts.read_tool_usage_tracker(ctx.scenario_name, known_tools=tool_names)
    if not isinstance(tracker, ToolUsageTracker):
        tracker = ToolUsageTracker(known_tools=tool_names)
    tracker.record_generation(ctx.generation, raw_text)
    artifacts.write_tool_usage_tracker(ctx.scenario_name, tracker)


def _hint_feedback_previous_best(ctx: GenerationContext) -> float:
    """Recover the pre-tournament best score for hint-reflection context."""
    if ctx.gate_decision == "advance":
        return max(0.0, ctx.previous_best - ctx.gate_delta)
    return ctx.previous_best


def _collect_hint_feedback(
    ctx: GenerationContext,
    *,
    agents: AgentOrchestrator | None,
    artifacts: ArtifactStore,
    sqlite: SQLiteStore,
    events: EventStreamEmitter,
) -> HintFeedback | None:
    """Collect post-tournament competitor feedback on the hints it actually used."""
    if ctx.settings.ablation_no_feedback or agents is None:
        return None
    tournament = ctx.tournament
    if tournament is None:
        return None
    hints_used = ctx.applied_competitor_hints.strip()
    if not hints_used:
        return None

    hint_items = prepare_hint_reflection_items(hints_used)
    prompt = build_hint_reflection_prompt(
        hints=hints_used,
        tournament_best_score=tournament.best_score,
        tournament_mean_score=tournament.mean_score,
        previous_best=_hint_feedback_previous_best(ctx),
        hint_items=hint_items,
    )
    try:
        client, resolved_model = agents.resolve_role_execution(
            "competitor",
            generation=ctx.generation,
            scenario_name=ctx.scenario_name,
        )
        model = resolved_model or agents.competitor.model
        response = client.generate(
            model=model,
            prompt=prompt,
            max_tokens=400,
            temperature=0.2,
            role="competitor",
        )
    except Exception:
        logger.debug("competitor hint feedback collection failed", exc_info=True)
        return None

    exec_result = RoleExecution(
        role="competitor_hint_feedback",
        content=response.text,
        usage=RoleUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=response.usage.latency_ms,
            model=response.usage.model,
        ),
        subagent_id="competitor_hint_feedback",
        status="completed",
    )
    sqlite.append_generation_agent_activity(
        ctx.run_id,
        ctx.generation,
        outputs=[("competitor_hint_feedback", exec_result.content)],
        role_metrics=[
            (
                exec_result.role,
                exec_result.usage.model,
                exec_result.usage.input_tokens,
                exec_result.usage.output_tokens,
                exec_result.usage.latency_ms,
                exec_result.subagent_id,
                exec_result.status,
            )
        ],
    )

    feedback = parse_hint_feedback(
        response.text,
        generation=ctx.generation,
        hint_items=hint_items,
    )
    if feedback.is_empty():
        return None

    artifacts.write_hint_feedback(ctx.scenario_name, ctx.generation, feedback)
    events.emit(
        "hint_feedback_collected",
        {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "helpful_count": len(feedback.helpful),
            "misleading_count": len(feedback.misleading),
            "missing_count": len(feedback.missing),
        },
    )
    return feedback


def _hint_volume_policy(ctx: GenerationContext) -> HintVolumePolicy:
    return HintVolumePolicy(
        max_hints=ctx.settings.hint_volume_max_hints,
        archive_rotated=ctx.settings.hint_volume_archive_rotated,
    )


def _hint_feedback_matches(text: str, candidate: str) -> bool:
    left = text.strip().lower()
    right = candidate.strip().lower()
    return bool(left and right and (left == right or left in right or right in left))


def _apply_hint_feedback_to_manager(manager: HintManager, feedback: HintFeedback | None) -> None:
    if feedback is None:
        return
    for helpful in feedback.helpful:
        for hint in manager.active_hints() + manager.archived_hints():
            if _hint_feedback_matches(hint.text, helpful):
                manager.update_impact(hint.text, max(hint.impact_score, 0.9))
    for misleading in feedback.misleading:
        for hint in manager.active_hints() + manager.archived_hints():
            if _hint_feedback_matches(hint.text, misleading):
                manager.update_impact(hint.text, min(hint.impact_score, 0.1))
