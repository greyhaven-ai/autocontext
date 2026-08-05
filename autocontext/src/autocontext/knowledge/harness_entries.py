"""Typed harness entries with scoped CRUD edits and rollback (AC-898).

Lessons, policies, procedures, and delegation specs persisted as typed,
scoped, versioned entries instead of accumulated prose. Every mutation is
a CRUD edit recorded to an append-only refinement history with
before/after snapshots, so any refinement can be rolled back. Modeled on
prime-agent's continual-harness refinement store; adds outcome marking so
verifier-scored runs can confirm or refute an entry's expected outcome.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

HarnessEntryKind = Literal["policy", "fact", "procedure", "delegation"]
HarnessScope = Literal["run", "scenario_family", "global"]
HarnessOutcome = Literal["pending", "confirmed", "refuted"]

SCOPE_ORDER: dict[str, int] = {"run": 0, "scenario_family": 1, "global": 2}

STATE_FILE_NAME = "harness_state.json"
HISTORY_FILE_NAME = "harness_refinements.jsonl"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class HarnessEntry(BaseModel):
    """One typed, scoped, versioned harness entry."""

    id: str
    kind: HarnessEntryKind
    scope: HarnessScope
    title: str
    content: str
    expected_outcome: str = ""
    outcome: HarnessOutcome = "pending"
    outcome_evidence: str = ""
    source: str = ""
    created_at: str = ""
    updated_at: str = ""
    version: int = 1


class HarnessEdit(BaseModel):
    """A single create/update/delete request against the store."""

    action: Literal["create", "update", "delete"]
    kind: HarnessEntryKind
    id: str = ""
    title: str = ""
    content: str = ""
    expected_outcome: str = ""
    reason: str = ""


class AppliedHarnessEdit(BaseModel):
    """An edit plus what actually happened when it was applied."""

    edit: HarnessEdit
    entry_id: str
    applied: bool
    error: str = ""
    before: HarnessEntry | None = None
    after: HarnessEntry | None = None


class HarnessRefinement(BaseModel):
    """One recorded refinement: a batch of applied edits at one scope."""

    id: str
    scope: HarnessScope
    summary: str = ""
    applied_edits: list[AppliedHarnessEdit] = Field(default_factory=list)
    rollback_of: str = ""
    created_at: str = ""


class HarnessEntryStore:
    """JSON-state + JSONL-history store for typed harness entries."""

    def __init__(self, root: Path | str, *, now_iso: Callable[[], str] = _now_iso) -> None:
        self.root = Path(root)
        self._now_iso = now_iso

    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILE_NAME

    @property
    def history_path(self) -> Path:
        return self.root / HISTORY_FILE_NAME

    def apply(
        self,
        edits: Sequence[HarnessEdit],
        *,
        scope: HarnessScope,
        summary: str = "",
        source: str = "",
        rollback_of: str = "",
    ) -> HarnessRefinement:
        """Apply a batch of edits at one scope; record and return the refinement."""
        state = self._load_state()
        applied = [self._apply_edit(state, edit, scope=scope, source=source) for edit in edits]
        refinement = HarnessRefinement(
            id=f"refinement_{uuid.uuid4().hex[:8]}",
            scope=scope,
            summary=summary,
            applied_edits=applied,
            rollback_of=rollback_of,
            created_at=self._now_iso(),
        )
        self._save_state(state)
        self._append_history(refinement)
        return refinement

    def entries(
        self,
        *,
        kind: HarnessEntryKind | None = None,
        scope: HarnessScope | None = None,
    ) -> list[HarnessEntry]:
        out = list(self._load_state().values())
        if kind is not None:
            out = [entry for entry in out if entry.kind == kind]
        if scope is not None:
            out = [entry for entry in out if entry.scope == scope]
        return sorted(out, key=lambda entry: (entry.created_at, entry.id))

    def load_history(self) -> list[HarnessRefinement]:
        """Refinements in append order; malformed lines are skipped so one torn append cannot break rollback."""
        if not self.history_path.exists():
            return []
        out: list[HarnessRefinement] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                out.append(HarnessRefinement.model_validate_json(stripped))
            except (ValidationError, ValueError):
                continue
        return out

    def _apply_edit(
        self,
        state: dict[str, HarnessEntry],
        edit: HarnessEdit,
        *,
        scope: HarnessScope,
        source: str,
    ) -> AppliedHarnessEdit:
        if edit.action == "create":
            entry_id = edit.id or f"harness_{uuid.uuid4().hex[:8]}"
            if entry_id in state:
                return AppliedHarnessEdit(edit=edit, entry_id=entry_id, applied=False, error="duplicate_id")
            now = self._now_iso()
            entry = HarnessEntry(
                id=entry_id,
                kind=edit.kind,
                scope=scope,
                title=edit.title,
                content=edit.content,
                expected_outcome=edit.expected_outcome,
                source=source,
                created_at=now,
                updated_at=now,
            )
            state[entry_id] = entry
            return AppliedHarnessEdit(edit=edit, entry_id=entry_id, applied=True, after=entry)

        existing = state.get(edit.id)
        if existing is None:
            return AppliedHarnessEdit(edit=edit, entry_id=edit.id, applied=False, error="not_found")
        before = existing.model_copy(deep=True)
        if edit.action == "delete":
            del state[edit.id]
            return AppliedHarnessEdit(edit=edit, entry_id=edit.id, applied=True, before=before)
        updated = existing.model_copy(deep=True)
        if edit.title:
            updated.title = edit.title
        if edit.content:
            updated.content = edit.content
        if edit.expected_outcome:
            updated.expected_outcome = edit.expected_outcome
        updated.updated_at = self._now_iso()
        updated.version += 1
        state[edit.id] = updated
        return AppliedHarnessEdit(edit=edit, entry_id=edit.id, applied=True, before=before, after=updated)

    def _load_state(self) -> dict[str, HarnessEntry]:
        """Corrupt or unreadable state degrades to empty; the next save rewrites it cleanly."""
        if not self.state_path.exists():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        entries_raw = raw.get("entries")
        if not isinstance(entries_raw, dict):
            return {}
        entries: dict[str, HarnessEntry] = {}
        for entry_id, data in entries_raw.items():
            try:
                entries[entry_id] = HarnessEntry.model_validate(data)
            except ValidationError:
                continue
        return entries

    def _save_state(self, entries: dict[str, HarnessEntry]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "entries": {entry_id: entry.model_dump() for entry_id, entry in sorted(entries.items())},
        }
        tmp = self.state_path.with_name(f"{STATE_FILE_NAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.state_path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _append_history(self, refinement: HarnessRefinement) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(refinement.model_dump_json() + "\n")
