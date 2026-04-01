"""Stage helpers — freshness (extracted from stages.py, AC-482)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autocontext.knowledge.evidence_freshness import (
    EvidenceFreshness,
    FreshnessPolicy,
    apply_freshness_decay,
    detect_stale_context,
)
from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy
from autocontext.loop.stage_types import GenerationContext
from autocontext.notebook.types import SessionNotebook

if TYPE_CHECKING:
    from autocontext.storage import ArtifactStore


def _freshness_policy(ctx: GenerationContext) -> FreshnessPolicy:
    return FreshnessPolicy(
        max_age_gens=ctx.settings.evidence_freshness_max_age_gens,
        min_confidence=ctx.settings.evidence_freshness_min_confidence,
        min_support=ctx.settings.evidence_freshness_min_support,
    )


def _format_freshness_warning_block(label: str, warnings: list[str]) -> str:
    if not warnings:
        return ""
    return f"{label} freshness warnings:\n" + "\n".join(f"- {warning}" for warning in warnings)


def _load_fresh_skill_context(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> tuple[str, str]:
    lessons = artifacts.lesson_store.read_lessons(ctx.scenario_name)
    if not lessons:
        return artifacts.read_skills(ctx.scenario_name), ""

    records: list[tuple[str, EvidenceFreshness]] = []
    for lesson in lessons:
        records.append((
            lesson.text.strip(),
            EvidenceFreshness(
                item_id=lesson.id or lesson.text.strip(),
                support_count=1,
                last_validated_gen=max(lesson.meta.last_validated_gen, 0),
                confidence=max(0.0, min(1.0, lesson.meta.best_score)),
                created_at_gen=max(lesson.meta.generation, 0),
            ),
        ))

    items = [item for _, item in records]
    active, _ = apply_freshness_decay(items, ctx.generation, _freshness_policy(ctx))
    active_ids = {item.item_id for item in active}
    active_text = "\n".join(
        text for text, item in records if item.item_id in active_ids
    ).strip()
    warnings = detect_stale_context(items, ctx.generation, _freshness_policy(ctx))
    return active_text, _format_freshness_warning_block("Lesson", warnings)


def _load_fresh_hint_context(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> tuple[str, str]:
    if not ctx.settings.hint_volume_enabled:
        return ctx.coach_competitor_hints, ""

    raw_manager = artifacts.read_hint_manager(
        ctx.scenario_name,
        policy=HintVolumePolicy(
            max_hints=ctx.settings.hint_volume_max_hints,
            archive_rotated=ctx.settings.hint_volume_archive_rotated,
        ),
    )
    manager = raw_manager if isinstance(raw_manager, HintManager) else HintManager(HintVolumePolicy())
    ranked_hints = manager.active_hints()
    if not ranked_hints:
        return "", ""

    records: list[tuple[str, EvidenceFreshness]] = []
    for hint in ranked_hints:
        records.append((
            hint.text,
            EvidenceFreshness(
                item_id=hint.text,
                support_count=1,
                last_validated_gen=max(hint.generation_added, 0),
                confidence=max(0.0, min(1.0, hint.impact_score)),
                created_at_gen=max(hint.generation_added, 0),
            ),
        ))

    items = [item for _, item in records]
    active, _ = apply_freshness_decay(items, ctx.generation, _freshness_policy(ctx))
    active_ids = {item.item_id for item in active}
    fresh_hints = "\n".join(
        f"- {text}" for text, item in records if item.item_id in active_ids
    ).strip()
    warnings = detect_stale_context(items, ctx.generation, _freshness_policy(ctx))
    return fresh_hints, _format_freshness_warning_block("Hint", warnings)


def _filter_notebook_by_freshness(
    ctx: GenerationContext,
    notebook: SessionNotebook,
) -> tuple[SessionNotebook, str]:
    last_validated_gen = notebook.best_generation if notebook.best_generation is not None else ctx.generation
    confidence = notebook.best_score if notebook.best_score is not None else 1.0
    fields = [
        "current_objective",
        "current_hypotheses",
        "unresolved_questions",
        "operator_observations",
        "follow_ups",
    ]
    records: list[tuple[str, EvidenceFreshness]] = []
    for field_name in fields:
        value = getattr(notebook, field_name)
        if not value:
            continue
        records.append((
            field_name,
            EvidenceFreshness(
                item_id=f"notebook:{field_name}",
                support_count=1,
                last_validated_gen=max(last_validated_gen, 0),
                confidence=max(0.0, min(1.0, confidence)),
                created_at_gen=max(last_validated_gen, 0),
            ),
        ))

    if not records:
        return notebook, ""

    items = [item for _, item in records]
    active, _ = apply_freshness_decay(items, ctx.generation, _freshness_policy(ctx))
    active_ids = {item.item_id for item in active}
    filtered = notebook.model_copy(update={
        "current_objective": (
            notebook.current_objective
            if "notebook:current_objective" in active_ids
            else ""
        ),
        "current_hypotheses": (
            notebook.current_hypotheses
            if "notebook:current_hypotheses" in active_ids
            else []
        ),
        "unresolved_questions": (
            notebook.unresolved_questions
            if "notebook:unresolved_questions" in active_ids
            else []
        ),
        "operator_observations": (
            notebook.operator_observations
            if "notebook:operator_observations" in active_ids
            else []
        ),
        "follow_ups": (
            notebook.follow_ups
            if "notebook:follow_ups" in active_ids
            else []
        ),
    })
    warnings = detect_stale_context(items, ctx.generation, _freshness_policy(ctx))
    return filtered, _format_freshness_warning_block("Notebook", warnings)
