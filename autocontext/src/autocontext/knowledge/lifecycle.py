"""Lesson lifecycle derived from live playbook/SKILL markdown primitives."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from autocontext.storage.artifacts import ArtifactStore

STALENESS_WINDOW = 10
LESSONS_START = "<!-- LESSONS_START -->"
LESSONS_END = "<!-- LESSONS_END -->"
STALE_MARKER = "<!-- autocontext:lesson-status=stale -->"


@dataclass(frozen=True)
class _DerivedLesson:
    id: str
    text: str
    status: Literal["active", "stale"]
    source: str


def _normalize_text(text: str) -> str:
    stripped = text.replace(STALE_MARKER, "").strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    return " ".join(stripped.split())


def _lesson_id(text: str) -> str:
    return "lesson_" + hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()[:12]


def _parse_lesson_line(line: str) -> tuple[str, bool] | None:
    stripped = line.strip()
    if not stripped.startswith("-"):
        return None
    text = _normalize_text(stripped[1:].strip())
    if not text:
        return None
    return text, STALE_MARKER in stripped


def _playbook_block(content: str) -> tuple[int, int, str] | None:
    start = content.find(LESSONS_START)
    end = content.find(LESSONS_END)
    if start == -1 or end == -1 or end <= start:
        return None
    block_start = start + len(LESSONS_START)
    return block_start, end, content[block_start:end]


def _playbook_lessons(artifacts: ArtifactStore, scenario: str) -> list[_DerivedLesson]:
    content = artifacts.read_playbook(scenario)
    block = _playbook_block(content)
    if block is None:
        return []
    lessons: list[_DerivedLesson] = []
    for line in block[2].splitlines():
        parsed = _parse_lesson_line(line)
        if parsed is None:
            continue
        text, stale = parsed
        lessons.append(
            _DerivedLesson(
                id=_lesson_id(text),
                text=text,
                status="stale" if stale else "active",
                source=f"knowledge/{scenario}/playbook.md",
            )
        )
    return lessons


def _skill_lessons(artifacts: ArtifactStore, scenario: str) -> list[_DerivedLesson]:
    lessons: list[_DerivedLesson] = []
    for line in artifacts.read_skill_lessons_raw(scenario):
        parsed = _parse_lesson_line(line)
        if parsed is None:
            continue
        text, stale = parsed
        lessons.append(
            _DerivedLesson(
                id=_lesson_id(text),
                text=text,
                status="stale" if stale else "active",
                source=f"skills/{scenario.replace('_', '-')}-ops/SKILL.md",
            )
        )
    return lessons


def _derived_lessons(artifacts: ArtifactStore, scenario: str) -> list[_DerivedLesson]:
    seen: set[str] = set()
    result: list[_DerivedLesson] = []
    for lesson in [*_playbook_lessons(artifacts, scenario), *_skill_lessons(artifacts, scenario)]:
        if lesson.id in seen:
            continue
        seen.add(lesson.id)
        result.append(lesson)
    return result


def _lesson_view(lesson: _DerivedLesson, current_generation: int) -> dict[str, Any]:
    return {
        "id": lesson.id,
        "text": lesson.text,
        "status": lesson.status,
        "generation": 0,
        "createdAt": "",
        "bestScore": None,
        "lastValidatedGen": current_generation if lesson.status == "active" else None,
        "supersededBy": None,
        "source": lesson.source,
    }


def _dead_end_views(dead_ends_md: str) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for block in dead_ends_md.split("### Dead End"):
        text = block.strip()
        if not text:
            continue
        did = "deadend_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
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
                "source": "knowledge/dead_ends.md",
            }
        )
    return views


def build_lifecycle(
    *,
    artifacts: ArtifactStore,
    scenario: str,
    current_generation: int,
    lesson_store: object | None = None,
    staleness_window: int = STALENESS_WINDOW,
) -> dict[str, Any]:
    del lesson_store, staleness_window
    active: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for lesson in _derived_lessons(artifacts, scenario):
        if lesson.status == "stale":
            stale.append(_lesson_view(lesson, current_generation))
        else:
            active.append(_lesson_view(lesson, current_generation))
    return {
        "scenario": scenario,
        "pending": [],
        "active": active,
        "stale": stale,
        "deadEnd": _dead_end_views(artifacts.read_dead_ends(scenario)),
    }


def approve_lesson(
    *,
    artifacts: ArtifactStore,
    scenario: str,
    lesson_id: str,
    current_generation: int,
    lesson_store: object | None = None,
) -> str | None:
    del lesson_store, current_generation
    found, _text = _set_lesson_stale(artifacts, scenario, lesson_id, stale=False)
    return "active" if found else None


def reject_lesson(
    *,
    artifacts: ArtifactStore,
    scenario: str,
    lesson_id: str,
    lesson_store: object | None = None,
) -> bool:
    del lesson_store
    found, _text = _remove_lesson(artifacts, scenario, lesson_id)
    return found


def curate_lesson(
    *,
    artifacts: ArtifactStore,
    scenario: str,
    lesson_id: str,
    action: str,
    current_generation: int,
    lesson_store: object | None = None,
) -> str | None:
    del lesson_store, current_generation
    if action == "delete":
        found, _text = _remove_lesson(artifacts, scenario, lesson_id)
        if found:
            return "deleted"
        removed, _entry = _remove_dead_end(artifacts, scenario, lesson_id)
        return "deleted" if removed else None
    if action == "stale":
        found, _text = _set_lesson_stale(artifacts, scenario, lesson_id, stale=True)
        return "stale" if found else None
    if action == "activate":
        found, _text = _set_lesson_stale(artifacts, scenario, lesson_id, stale=False)
        if found:
            return "active"
        removed, entry = _remove_dead_end(artifacts, scenario, lesson_id)
        if not removed or entry is None:
            return None
        _append_playbook_lesson(artifacts, scenario, entry)
        return "active"
    if action == "deadEnd":
        found, text = _remove_lesson(artifacts, scenario, lesson_id)
        if not found or text is None:
            return None
        artifacts.append_dead_end(scenario, text)
        return "deadEnd"
    return None


def _remove_lesson(artifacts: ArtifactStore, scenario: str, lesson_id: str) -> tuple[bool, str | None]:
    found_playbook, text = _rewrite_playbook_lessons(artifacts, scenario, lesson_id, mode="remove")
    found_skill, skill_text = _rewrite_skill_lessons(artifacts, scenario, lesson_id, mode="remove")
    return found_playbook or found_skill, text or skill_text


def _remove_dead_end(
    artifacts: ArtifactStore, scenario: str, lesson_id: str
) -> tuple[bool, str | None]:
    """Remove one dead-end block by its derived id; returns its text when found."""
    md = artifacts.read_dead_ends(scenario)
    kept: list[str] = []
    removed: str | None = None
    for block in md.split("### Dead End"):
        text = block.strip()
        if not text:
            continue
        did = "deadend_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        if removed is None and did == lesson_id:
            removed = text
            continue
        kept.append(text)
    if removed is None:
        return False, None
    rendered = "".join(f"### Dead End\n\n{text}\n\n" for text in kept)
    artifacts.replace_dead_ends(scenario, rendered)
    return True, removed


def _append_playbook_lesson(artifacts: ArtifactStore, scenario: str, text: str) -> None:
    """Append a lesson bullet to the playbook's lessons block (creating one if absent)."""
    content = artifacts.read_playbook(scenario)
    bullet = f"- {text}"
    block = _playbook_block(content)
    if block is None:
        suffix = f"\n{LESSONS_START}\n{bullet}\n{LESSONS_END}\n"
        artifacts.write_playbook(scenario, content + suffix)
        return
    block_start, end, body = block
    new_body = body.rstrip("\n") + f"\n{bullet}\n"
    artifacts.write_playbook(scenario, content[:block_start] + new_body + content[end:])


def _set_lesson_stale(
    artifacts: ArtifactStore,
    scenario: str,
    lesson_id: str,
    *,
    stale: bool,
) -> tuple[bool, str | None]:
    mode: Literal["stale", "active"] = "stale" if stale else "active"
    found_playbook, text = _rewrite_playbook_lessons(artifacts, scenario, lesson_id, mode=mode)
    found_skill, skill_text = _rewrite_skill_lessons(artifacts, scenario, lesson_id, mode=mode)
    return found_playbook or found_skill, text or skill_text


def _rewrite_playbook_lessons(
    artifacts: ArtifactStore,
    scenario: str,
    lesson_id: str,
    *,
    mode: Literal["remove", "stale", "active"],
) -> tuple[bool, str | None]:
    content = artifacts.read_playbook(scenario)
    block = _playbook_block(content)
    if block is None:
        return False, None
    start, end, body = block
    found = False
    changed = False
    target_text: str | None = None
    next_lines: list[str] = []
    for line in body.splitlines():
        parsed = _parse_lesson_line(line)
        if parsed is None:
            next_lines.append(line)
            continue
        text, is_stale = parsed
        if _lesson_id(text) != lesson_id:
            next_lines.append(line)
            continue
        found = True
        target_text = target_text or text
        if mode == "remove":
            changed = True
            continue
        if mode == "active" and not is_stale:
            next_lines.append(line)
            continue
        if mode == "stale" and is_stale:
            next_lines.append(line)
            continue
        changed = True
        bullet = f"- {text}"
        if mode == "stale":
            bullet = f"{bullet} {STALE_MARKER}"
        next_lines.append(bullet)
    if not found:
        return False, None
    if not changed:
        return True, target_text
    new_body = "\n".join(next_lines).strip()
    prefix = content[:start].rstrip()
    suffix = content[end:].lstrip()
    new_content = f"{prefix}\n{new_body}\n{suffix}" if new_body else f"{prefix}\n{suffix}"
    artifacts.write_playbook(scenario, new_content)
    return True, target_text


def _rewrite_skill_lessons(
    artifacts: ArtifactStore,
    scenario: str,
    lesson_id: str,
    *,
    mode: Literal["remove", "stale", "active"],
) -> tuple[bool, str | None]:
    lines = artifacts.read_skill_lessons_raw(scenario)
    if not lines:
        return False, None
    found = False
    changed = False
    target_text: str | None = None
    next_lines: list[str] = []
    for line in lines:
        parsed = _parse_lesson_line(line)
        if parsed is None:
            next_lines.append(line)
            continue
        text, is_stale = parsed
        if _lesson_id(text) != lesson_id:
            next_lines.append(line)
            continue
        found = True
        target_text = target_text or text
        if mode == "remove":
            changed = True
            continue
        if mode == "active" and not is_stale:
            next_lines.append(line)
            continue
        if mode == "stale" and is_stale:
            next_lines.append(line)
            continue
        changed = True
        bullet = f"- {text}"
        if mode == "stale":
            bullet = f"{bullet} {STALE_MARKER}"
        next_lines.append(bullet)
    if changed:
        artifacts.replace_skill_lessons(scenario, next_lines)
    return found, target_text
