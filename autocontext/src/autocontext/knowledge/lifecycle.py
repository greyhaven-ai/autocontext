"""Pure lesson-lifecycle assembly and curation ops (Cowork 2c).

Backed by the structured LessonStore (lessons.json) and the dead_ends.md
registry. "pending" is a status on the lesson itself (meta.approval_status),
not a separate store, so the whole lifecycle reads one store.
"""

from __future__ import annotations

import hashlib
from typing import Any

from autocontext.knowledge.lessons import Lesson, LessonStore
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
    scenario: str,
    current_generation: int,
    staleness_window: int = STALENESS_WINDOW,
) -> dict[str, Any]:
    pending: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for lesson in lesson_store.read_lessons(scenario):
        if lesson.is_pending():
            pending.append(_lesson_view(lesson, "pending"))
            continue
        if lesson.is_superseded():
            continue
        if lesson.is_stale(current_generation, staleness_window):
            stale.append(_lesson_view(lesson, "stale"))
        else:
            active.append(_lesson_view(lesson, "active"))
    return {
        "scenario": scenario,
        "pending": pending,
        "active": active,
        "stale": stale,
        "deadEnd": _dead_end_views(artifacts.read_dead_ends(scenario)),
    }


def approve_lesson(
    *,
    lesson_store: LessonStore,
    scenario: str,
    lesson_id: str,
    current_generation: int,
) -> str | None:
    """Approve a pending lesson: flip it to active. Returns None if not pending."""
    lessons = lesson_store.read_lessons(scenario)
    target = next((les for les in lessons if les.id == lesson_id and les.is_pending()), None)
    if target is None:
        return None
    target.meta.approval_status = "active"
    # Never lower the validation generation: approving must not make a lesson stale
    # (current_generation can be 0 when derived from an otherwise-empty store).
    target.meta.last_validated_gen = max(current_generation, target.meta.generation, target.meta.last_validated_gen)
    lesson_store.write_lessons(scenario, lessons)
    return "active"


def reject_lesson(*, lesson_store: LessonStore, scenario: str, lesson_id: str) -> bool:
    """Reject a PENDING lesson: remove it from the store. Returns False if the id is
    not a pending lesson. Deleting an already-active lesson is a separate, explicit
    action (curate "delete")."""
    lessons = lesson_store.read_lessons(scenario)
    target = next((les for les in lessons if les.id == lesson_id and les.is_pending()), None)
    if target is None:
        return False
    lesson_store.write_lessons(scenario, [les for les in lessons if les.id != lesson_id])
    return True


def curate_lesson(
    *,
    artifacts: ArtifactStore,
    lesson_store: LessonStore,
    scenario: str,
    lesson_id: str,
    action: str,
    current_generation: int,
) -> str | None:
    """Manually mark an active lesson stale, move it to dead-end, or delete it."""
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
