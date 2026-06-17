"""Per-scenario queue of lessons awaiting human approval (Cowork 2c)."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from autocontext.knowledge.lessons import Lesson

logger = logging.getLogger(__name__)


class PendingLessonStore:
    """JSON-backed queue of pending lessons, mirroring LessonStore's on-disk shape."""

    def __init__(self, knowledge_root: Path) -> None:
        self.knowledge_root = knowledge_root

    def _path(self, scenario: str) -> Path:
        from autocontext.storage.scenario_paths import resolve_scenario_root

        return resolve_scenario_root(self.knowledge_root, scenario) / "pending_lessons.json"

    def read(self, scenario: str) -> list[Lesson]:
        path = self._path(scenario)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            logger.debug("unable to read pending lessons for %s from %s", scenario, path)
            return []
        return [Lesson.from_dict(entry) for entry in data]

    def write(self, scenario: str, lessons: Sequence[Lesson]) -> None:
        path = self._path(scenario)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([les.to_dict() for les in lessons], indent=2),
            encoding="utf-8",
        )

    def add(self, scenario: str, lesson: Lesson) -> None:
        lessons = self.read(scenario)
        if any(existing.id == lesson.id or existing.text == lesson.text for existing in lessons):
            return
        lessons.append(lesson)
        self.write(scenario, lessons)

    def remove(self, scenario: str, lesson_id: str) -> Lesson | None:
        lessons = self.read(scenario)
        removed: Lesson | None = None
        kept: list[Lesson] = []
        for lesson in lessons:
            if lesson.id == lesson_id and removed is None:
                removed = lesson
            else:
                kept.append(lesson)
        if removed is not None:
            self.write(scenario, kept)
        return removed
