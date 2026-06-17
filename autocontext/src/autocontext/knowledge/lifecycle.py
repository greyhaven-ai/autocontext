"""Pure lesson-lifecycle assembly and curation ops (Cowork 2c).

Backed by the structured LessonStore (lessons.json), the PendingLessonStore
(pending_lessons.json), and the dead_ends.md registry.
"""

from __future__ import annotations

import hashlib
from typing import Any

from autocontext.knowledge.lessons import Lesson, LessonStore
from autocontext.knowledge.pending_lessons import PendingLessonStore
from autocontext.storage.artifacts import ArtifactStore

STALENESS_WINDOW = 10


def _lesson_view(lesson: Lesson, status: str, source: str = "curator") -> dict[str, Any]:
    return {
        "id": lesson.id,
        "text": lesson.text,
        "status": status,
        "generation": lesson.meta.generation,
        "createdAt": lesson.meta.created_at,
        "bestScore": lesson.meta.best_score,
        "lastValidatedGen": lesson.meta.last_validated_gen,
        "supersededBy": lesson.meta.superseded_by or None,
        "source": source,
    }


def _dead_end_views(dead_ends_md: str) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for block in dead_ends_md.split("### Dead End"):
        text = block.strip()
        if not text:
            continue
        did = "deadend_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        views.append(
            {
                "id": did,
                "text": text,
                "status": "deadEnd",
                "generation": 0,
                "createdAt": "",
                "bestScore": None,
                "lastValidatedGen": None,
                "supersededBy": None,
                "source": "curator",
            }
        )
    return views


def build_lifecycle(
    *,
    artifacts: ArtifactStore,
    lesson_store: LessonStore,
    pending_store: PendingLessonStore,
    scenario: str,
    current_generation: int,
    staleness_window: int = STALENESS_WINDOW,
) -> dict[str, Any]:
    active: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for lesson in lesson_store.read_lessons(scenario):
        if lesson.is_superseded():
            continue
        if lesson.is_stale(current_generation, staleness_window):
            stale.append(_lesson_view(lesson, "stale"))
        else:
            active.append(_lesson_view(lesson, "active"))
    pending = [_lesson_view(lesson, "pending") for lesson in pending_store.read(scenario)]
    dead_end = _dead_end_views(artifacts.read_dead_ends(scenario))
    return {
        "scenario": scenario,
        "pending": pending,
        "active": active,
        "stale": stale,
        "deadEnd": dead_end,
    }


def approve_lesson(
    *,
    lesson_store: LessonStore,
    pending_store: PendingLessonStore,
    scenario: str,
    lesson_id: str,
    current_generation: int,
) -> str | None:
    """Move a pending lesson to active. Idempotent: returns None if not pending."""
    moved = pending_store.remove(scenario, lesson_id)
    if moved is None:
        return None
    existing = lesson_store.read_lessons(scenario)
    if not any(les.id == moved.id or les.text == moved.text for les in existing):
        moved.meta.last_validated_gen = current_generation
        lesson_store.write_lessons(scenario, [*existing, moved])
    return "active"


def reject_lesson(
    *,
    lesson_store: LessonStore,
    pending_store: PendingLessonStore,
    scenario: str,
    lesson_id: str,
) -> bool:
    """Discard from pending and remove from active if present (review mode)."""
    removed_pending = pending_store.remove(scenario, lesson_id)
    existing = lesson_store.read_lessons(scenario)
    kept = [les for les in existing if les.id != lesson_id]
    removed_active = len(kept) != len(existing)
    if removed_active:
        lesson_store.write_lessons(scenario, kept)
    return removed_pending is not None or removed_active


def curate_lesson(
    *,
    artifacts: ArtifactStore,
    lesson_store: LessonStore,
    scenario: str,
    lesson_id: str,
    action: str,
    current_generation: int,
) -> str | None:
    """Manually mark an active lesson stale, move to dead-end, or delete it."""
    lessons = lesson_store.read_lessons(scenario)
    target = next((les for les in lessons if les.id == lesson_id), None)
    if target is None:
        return None
    if action == "delete":
        lesson_store.write_lessons(scenario, [les for les in lessons if les.id != lesson_id])
        return "deleted"
    if action == "stale":
        target.meta.last_validated_gen = -1
        lesson_store.write_lessons(scenario, lessons)
        return "stale"
    if action == "deadEnd":
        artifacts.append_dead_end(scenario, target.text)
        lesson_store.write_lessons(scenario, [les for les in lessons if les.id != lesson_id])
        return "deadEnd"
    return None
