"""AC-236: Schema- and state-aware lesson applicability.

Defines structured lessons with applicability metadata, a JSON-backed
LessonStore, and filtering/invalidation operations.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

_UNSET_GEN = -999_999


@dataclass(slots=True)
class ApplicabilityMeta:
    """Metadata tracking when and where a lesson was learned."""

    created_at: str
    generation: int
    best_score: float
    schema_version: str = ""
    upstream_sig: str = ""
    operation_type: str = "advance"
    superseded_by: str = ""
    last_validated_gen: int = _UNSET_GEN

    def __post_init__(self) -> None:
        if self.last_validated_gen == _UNSET_GEN:
            self.last_validated_gen = self.generation

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "generation": self.generation,
            "best_score": self.best_score,
            "schema_version": self.schema_version,
            "upstream_sig": self.upstream_sig,
            "operation_type": self.operation_type,
            "superseded_by": self.superseded_by,
            "last_validated_gen": self.last_validated_gen,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApplicabilityMeta:
        gen = int(data.get("generation", 0))
        return cls(
            created_at=str(data.get("created_at", "")),
            generation=gen,
            best_score=float(data.get("best_score", 0.0)),
            schema_version=str(data.get("schema_version", "")),
            upstream_sig=str(data.get("upstream_sig", "")),
            operation_type=str(data.get("operation_type", "advance")),
            superseded_by=str(data.get("superseded_by", "")),
            last_validated_gen=int(data.get("last_validated_gen", gen)),
        )


@dataclass(slots=True)
class Lesson:
    """A lesson with applicability metadata."""

    id: str
    text: str
    meta: ApplicabilityMeta

    def is_stale(self, current_generation: int, staleness_window: int = 10) -> bool:
        """A lesson is stale if not validated within staleness_window generations."""
        if self.meta.last_validated_gen < 0:
            return True
        return (current_generation - self.meta.last_validated_gen) > staleness_window

    def is_superseded(self) -> bool:
        return bool(self.meta.superseded_by)

    def is_applicable(self, current_generation: int, staleness_window: int = 10) -> bool:
        return not self.is_stale(current_generation, staleness_window) and not self.is_superseded()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "meta": self.meta.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lesson:
        return cls(
            id=str(data.get("id", "")),
            text=str(data.get("text", "")),
            meta=ApplicabilityMeta.from_dict(data.get("meta", {})),
        )


class LessonStore:
    """JSON-backed store for structured lessons with applicability metadata."""

    def __init__(self, knowledge_root: Path, skills_root: Path) -> None:
        self.knowledge_root = knowledge_root
        self.skills_root = skills_root

    def _lessons_path(self, scenario: str) -> Path:
        return self.knowledge_root / scenario / "lessons.json"

    def read_lessons(self, scenario: str) -> list[Lesson]:
        path = self._lessons_path(scenario)
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
            if not isinstance(raw, str):
                return []
            data = json.loads(raw)
            if not isinstance(data, list):
                return []
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            LOGGER.debug("unable to read structured lessons for %s from %s", scenario, path)
            return []
        return [Lesson.from_dict(entry) for entry in data]

    def current_generation(self, scenario: str) -> int:
        """Best-effort current generation derived from structured lessons."""
        lessons = self.read_lessons(scenario)
        if not lessons:
            return 0
        return max(
            max(lesson.meta.generation, lesson.meta.last_validated_gen)
            for lesson in lessons
        )

    def write_lessons(self, scenario: str, lessons: list[Lesson]) -> None:
        path = self._lessons_path(scenario)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([les.to_dict() for les in lessons], indent=2),
            encoding="utf-8",
        )

    def add_lesson(self, scenario: str, text: str, meta: ApplicabilityMeta) -> Lesson:
        lessons = self.read_lessons(scenario)
        lesson_id = f"lesson_{uuid.uuid4().hex[:8]}"
        lesson = Lesson(id=lesson_id, text=text, meta=meta)
        lessons.append(lesson)
        self.write_lessons(scenario, lessons)
        return lesson

    def get_applicable_lessons(
        self, scenario: str, current_generation: int, staleness_window: int = 10,
    ) -> list[Lesson]:
        return [
            les for les in self.read_lessons(scenario)
            if les.is_applicable(current_generation, staleness_window)
        ]

    def get_stale_lessons(
        self, scenario: str, current_generation: int, staleness_window: int = 10,
    ) -> list[Lesson]:
        return [
            les for les in self.read_lessons(scenario)
            if les.is_stale(current_generation, staleness_window) and not les.is_superseded()
        ]

    def invalidate_by_schema_change(self, scenario: str, new_schema_version: str) -> list[Lesson]:
        """Mark all lessons from older schema versions as stale (last_validated_gen = -1)."""
        lessons = self.read_lessons(scenario)
        invalidated: list[Lesson] = []
        for lesson in lessons:
            if lesson.meta.schema_version != new_schema_version:
                lesson.meta.last_validated_gen = -1
                invalidated.append(lesson)
        if invalidated:
            self.write_lessons(scenario, lessons)
        return invalidated

    def supersede_lesson(self, scenario: str, old_id: str, new_id: str) -> None:
        lessons = self.read_lessons(scenario)
        changed = False
        for lesson in lessons:
            if lesson.id == old_id:
                lesson.meta.superseded_by = new_id
                changed = True
                break
        if changed:
            self.write_lessons(scenario, lessons)

    def validate_lesson(self, scenario: str, lesson_id: str, current_generation: int) -> None:
        """Refresh last_validated_gen for a lesson."""
        lessons = self.read_lessons(scenario)
        changed = False
        for lesson in lessons:
            if lesson.id == lesson_id:
                lesson.meta.last_validated_gen = current_generation
                changed = True
                break
        if changed:
            self.write_lessons(scenario, lessons)

    def migrate_from_raw_bullets(
        self,
        scenario: str,
        raw_bullets: list[str],
        generation: int,
        best_score: float,
    ) -> list[Lesson]:
        """Migrate raw bullet strings into structured lessons. Idempotent — skips if lessons.json exists."""
        if self._lessons_path(scenario).exists():
            return []
        lessons: list[Lesson] = []
        for bullet in raw_bullets:
            meta = ApplicabilityMeta(
                created_at="",
                generation=generation,
                best_score=best_score,
                operation_type="migration",
            )
            lesson_id = f"lesson_{uuid.uuid4().hex[:8]}"
            lessons.append(Lesson(id=lesson_id, text=bullet, meta=meta))
        if lessons:
            self.write_lessons(scenario, lessons)
        return lessons

    def staleness_report(
        self, scenario: str, current_generation: int, staleness_window: int = 10,
    ) -> str:
        """Generate a markdown staleness report for operator visibility."""
        lessons = self.read_lessons(scenario)
        if not lessons:
            return "No lessons recorded."

        applicable = [les for les in lessons if les.is_applicable(current_generation, staleness_window)]
        stale = [
            les for les in lessons
            if les.is_stale(current_generation, staleness_window) and not les.is_superseded()
        ]
        superseded = [les for les in lessons if les.is_superseded()]

        lines = [
            "## Lesson Health",
            f"- Total: {len(lessons)}",
            f"- Applicable: {len(applicable)}",
        ]

        if stale:
            lines.append(f"- Stale: {len(stale)}")
            for entry in stale:
                lines.append(f"  - [{entry.id}] {entry.text} (last validated gen {entry.meta.last_validated_gen})")

        if superseded:
            lines.append(f"- Superseded: {len(superseded)}")
            for entry in superseded:
                lines.append(f"  - [{entry.id}] {entry.text} (superseded by {entry.meta.superseded_by})")

        return "\n".join(lines)
