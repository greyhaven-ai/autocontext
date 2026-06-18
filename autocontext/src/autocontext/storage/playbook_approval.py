"""Pending playbook approval gate helpers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any, Protocol

from autocontext.storage.scenario_paths import resolve_scenario_root
from autocontext.util.json_io import read_json, write_json


class LessonApprovalStore(Protocol):
    def read_lessons(self, scenario: str) -> list[Any]: ...
    def write_lessons(self, scenario: str, lessons: list[Any]) -> None: ...


class PlaybookApprovalHost(Protocol):
    knowledge_root: Path

    @property
    def lesson_store(self) -> LessonApprovalStore: ...

    def write_playbook(self, scenario_name: str, content: str) -> None: ...
    def persist_skill_note(self, scenario_name: str, generation_index: int, decision: str, lessons: str) -> None: ...
    def _append_mutation(
        self,
        scenario_name: str,
        *,
        mutation_type: str,
        payload: dict[str, Any],
        generation: int = 0,
        run_id: str = "",
        description: str = "",
    ) -> None: ...


class PlaybookApprovalMethods:
    def write_or_stage_playbook(
        self: PlaybookApprovalHost,
        scenario_name: str,
        content: str,
        *,
        require_playbook_approval: bool,
        source_run_id: str,
        generation: int,
        curator_decision: str = "advance",
    ) -> str:
        if not require_playbook_approval:
            self.write_playbook(scenario_name, content)
            if read_pending_playbook(self.knowledge_root, scenario_name)["has_pending"]:
                reject_pending_playbook(self.knowledge_root, scenario_name)
                self._append_mutation(
                    scenario_name,
                    mutation_type="playbook_pending_superseded",
                    payload={"generation": generation, "source_run_id": source_run_id},
                    description="Pending playbook cleared by live auto-mode update",
                )
            return "live"
        if read_pending_playbook(self.knowledge_root, scenario_name)["has_pending"]:
            self._append_mutation(
                scenario_name,
                mutation_type="playbook_awaiting_approval",
                payload={"generation": generation, "source_run_id": source_run_id},
                description="Playbook update skipped while prior update awaits approval",
            )
            return "awaiting_approval"
        result = stage_pending_playbook(
            self.knowledge_root,
            scenario_name,
            content,
            source_run_id=source_run_id,
            generation=generation,
            curator_decision=curator_decision,
        )
        self._append_mutation(
            scenario_name,
            mutation_type="playbook_pending",
            payload={"generation": generation, "source_run_id": source_run_id},
            description="Playbook update pending approval",
        )
        return result

    def read_pending_playbook(self: PlaybookApprovalHost, scenario_name: str) -> dict[str, Any]:
        return read_pending_playbook(self.knowledge_root, scenario_name)

    def approve_pending_playbook(self: PlaybookApprovalHost, scenario_name: str) -> dict[str, Any]:
        pending = read_pending_playbook(self.knowledge_root, scenario_name)
        result = approve_pending_playbook(self.knowledge_root, scenario_name, self.write_playbook)
        if result["ok"]:
            generation = int((pending.get("provenance") or {}).get("generation", 0))
            lessons = _extract_lesson_bullets(str(pending.get("content") or ""))
            if lessons:
                self.persist_skill_note(scenario_name, generation, "advance", "\n".join(lessons))
            self._append_mutation(scenario_name, mutation_type="playbook_approved", payload={}, description="Playbook approved")
        return result

    def reject_pending_playbook(self: PlaybookApprovalHost, scenario_name: str) -> dict[str, Any]:
        result = reject_pending_playbook(self.knowledge_root, scenario_name)
        if result["ok"]:
            self._append_mutation(scenario_name, mutation_type="playbook_rejected", payload={}, description="Playbook rejected")
        return result


def stage_pending_playbook(
    knowledge_root: Path,
    scenario_name: str,
    content: str,
    *,
    source_run_id: str,
    generation: int,
    curator_decision: str,
    created_at: str | None = None,
) -> str:
    scenario_dir = resolve_scenario_root(knowledge_root, scenario_name)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    if _pending_md(scenario_dir).exists() or _pending_json(scenario_dir).exists():
        raise ValueError("pending playbook already exists; approve or reject it before staging another")
    normalized = content.strip() + "\n"
    _pending_md(scenario_dir).write_text(normalized, encoding="utf-8")
    write_json(
        _pending_json(scenario_dir),
        {
            "schema_version": 1,
            "scenario_name": scenario_name,
            "source_run_id": source_run_id,
            "generation": generation,
            "curator_decision": curator_decision,
            "created_at": created_at or datetime.now(UTC).isoformat(),
            "status": "pending",
        },
    )
    return "pending"


def read_pending_playbook(knowledge_root: Path, scenario_name: str) -> dict[str, Any]:
    scenario_dir = resolve_scenario_root(knowledge_root, scenario_name)
    pending_path = _pending_md(scenario_dir)
    provenance_path = _pending_json(scenario_dir)
    if not pending_path.exists() or not provenance_path.exists():
        return {"has_pending": False, "content": "", "diff": "", "provenance": None}
    content = pending_path.read_text(encoding="utf-8")
    live_path = scenario_dir / "playbook.md"
    live = live_path.read_text(encoding="utf-8") if live_path.exists() else ""
    diff = "".join(
        unified_diff(
            live.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile="playbook.md",
            tofile="playbook.pending.md",
        )
    )
    return {
        "has_pending": True,
        "content": content,
        "diff": diff,
        "provenance": read_json(provenance_path),
    }


def approve_pending_playbook(
    knowledge_root: Path,
    scenario_name: str,
    write_live_playbook: Callable[[str, str], None],
) -> dict[str, Any]:
    pending = read_pending_playbook(knowledge_root, scenario_name)
    if not pending["has_pending"]:
        return {"ok": False, "status": "missing"}
    write_live_playbook(scenario_name, str(pending["content"]))
    _clear_pending(resolve_scenario_root(knowledge_root, scenario_name))
    return {"ok": True, "status": "approved"}


def reject_pending_playbook(
    knowledge_root: Path,
    scenario_name: str,
) -> dict[str, Any]:
    pending = read_pending_playbook(knowledge_root, scenario_name)
    if not pending["has_pending"]:
        return {"ok": False, "status": "missing"}
    _clear_pending(resolve_scenario_root(knowledge_root, scenario_name))
    return {"ok": True, "status": "rejected"}


def _extract_lesson_bullets(content: str) -> list[str]:
    start_marker = "<!-- LESSONS_START -->"
    end_marker = "<!-- LESSONS_END -->"
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start == -1 or end == -1 or end <= start:
        return []
    block = content[start + len(start_marker):end]
    return [line.strip() for line in block.splitlines() if line.strip().startswith("-")]


def _clear_pending(scenario_dir: Path) -> None:
    for path in (_pending_md(scenario_dir), _pending_json(scenario_dir)):
        path.unlink(missing_ok=True)


def _pending_md(scenario_dir: Path) -> Path:
    return scenario_dir / "playbook.pending.md"


def _pending_json(scenario_dir: Path) -> Path:
    return scenario_dir / "playbook.pending.json"


__all__ = [
    "PlaybookApprovalMethods",
    "approve_pending_playbook",
    "read_pending_playbook",
    "reject_pending_playbook",
    "stage_pending_playbook",
]
