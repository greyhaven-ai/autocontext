"""Typed harness entries with scoped CRUD edits and rollback (AC-898).

Lessons, policies, procedures, and delegation specs persisted as typed,
scoped, versioned entries instead of accumulated prose. Every mutation is
a CRUD edit recorded to an append-only refinement history with
before/after snapshots, so any refinement can be rolled back. Modeled on
prime-agent's continual-harness refinement store; adds outcome marking so
verifier-scored runs can confirm or refute an entry's expected outcome.

The scope guardrail (a narrower-scope caller cannot mutate broader-scope
state) covers every write path: ``apply``, ``mark_outcome``, and
``rollback`` (AC-907).

The store assumes a single writer per root directory: writes are atomic
but load-modify-save, so concurrent writers are last-writer-wins. State
files are per-language (the TypeScript mirror writes camelCase fields)
and are not interchangeable across the two runtimes.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

HarnessEntryKind = Literal["policy", "fact", "procedure", "delegation"]
HarnessScope = Literal["run", "scenario_family", "global"]
HarnessOutcome = Literal["pending", "confirmed", "refuted"]

SCOPE_ORDER: dict[str, int] = {"run": 0, "scenario_family": 1, "global": 2}

STATE_FILE_NAME = "harness_state.json"
HISTORY_FILE_NAME = "harness_refinements.jsonl"

KIND_HEADINGS: dict[str, str] = {
    "policy": "Policies",
    "fact": "Facts",
    "procedure": "Procedures",
    "delegation": "Delegations",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SkillReference(BaseModel):
    """Executable payload for a procedure entry (AC-899).

    A promoted skill carries real source code plus how to call it. Only the
    call pattern belongs in prompts; the source goes into execution assembly.
    """

    language: Literal["python"] = "python"
    entrypoint: str = Field(min_length=1)
    source: str = Field(min_length=1)
    call_pattern: str = ""
    # TS mirror names this field argumentsDescription; state files are
    # per-language and not interchangeable, so the divergence is intentional.
    arguments: dict[str, str] = Field(default_factory=dict)


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
    reference: SkillReference | None = None

    @model_validator(mode="after")
    def _reference_requires_procedure(self) -> HarnessEntry:
        if self.reference is not None and self.kind != "procedure":
            raise ValueError("reference is only valid on procedure entries")
        return self


class HarnessEdit(BaseModel):
    """A single create/update/delete request against the store."""

    action: Literal["create", "update", "delete"]
    kind: HarnessEntryKind
    id: str = ""
    title: str = ""
    content: str = ""
    expected_outcome: str = ""
    reason: str = ""
    reference: SkillReference | None = None

    @model_validator(mode="after")
    def _reference_requires_procedure_edit(self) -> HarnessEdit:
        if self.reference is not None and self.kind != "procedure":
            raise ValueError("reference is only valid on procedure edits")
        return self


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
        """Apply a batch of edits at one scope; record and return the refinement.

        An empty batch is a no-op: nothing is persisted and the returned
        refinement is not recorded in history.
        """
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
        if not edits:
            return refinement
        self._append_history(refinement)
        self._save_state(state)
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

    def mark_outcome(
        self,
        entry_id: str,
        outcome: Literal["confirmed", "refuted"],
        *,
        scope: HarnessScope,
        evidence: str = "",
    ) -> HarnessEntry:
        """Record a measured outcome for an entry's expected_outcome.

        Outcome marks are measurements, not refinements: they update state
        in place and are not recorded to the refinement history. The same
        scope guardrail as ``apply`` holds: a narrower-scope caller cannot
        mark a broader-scope entry.
        """
        state = self._load_state()
        existing = state.get(entry_id)
        if existing is None:
            raise ValueError(f"unknown harness entry: {entry_id}")
        if SCOPE_ORDER[existing.scope] > SCOPE_ORDER[scope]:
            raise ValueError(f"scope_readonly: {entry_id} is {existing.scope}-scoped")
        updated = existing.model_copy(deep=True)
        updated.outcome = outcome
        if evidence:
            updated.outcome_evidence = evidence
        updated.updated_at = self._now_iso()
        updated.version += 1
        state[entry_id] = updated
        self._save_state(state)
        return updated

    def render_markdown(self, *, kinds: Sequence[HarnessEntryKind] | None = None) -> str:
        """Markdown for prompt injection: grouped by kind, refuted entries excluded."""
        selected: Sequence[str] = kinds if kinds is not None else list(KIND_HEADINGS)
        sections: list[str] = []
        for kind in selected:
            visible = [entry for entry in self.entries() if entry.kind == kind and entry.outcome != "refuted"]
            if not visible:
                continue
            lines = [f"### {KIND_HEADINGS[kind]}"]
            for entry in visible:
                content = entry.content.replace("\n", "\n  ")
                line = f"- [{entry.id}] {entry.title}: {content}"
                if entry.expected_outcome:
                    line += f" (expected: {entry.expected_outcome})"
                lines.append(line)
            sections.append("\n".join(lines))
        if not sections:
            return ""
        return "## Harness Entries\n\n" + "\n\n".join(sections) + "\n"

    def rollback(self, refinement_id: str, *, scope: HarnessScope) -> HarnessRefinement:
        """Invert a recorded refinement by restoring its before-snapshots.

        One-step semantics: snapshots are restored blindly, so edits made
        to the same entries by later refinements are overwritten (lost
        update). Outcome marks are measurements, not refinement effects,
        so a current non-pending outcome survives the restore.

        The same scope guardrail as ``apply`` and ``mark_outcome`` holds
        (AC-907): a narrower-scope caller cannot roll back a broader-scope
        refinement. Checking the refinement's scope suffices because every
        entry a refinement touched has scope at or below the refinement's
        (``apply`` creates at caller scope and rejects broader updates).
        """
        target = next((r for r in self.load_history() if r.id == refinement_id), None)
        if target is None:
            raise ValueError(f"unknown refinement: {refinement_id}")
        if SCOPE_ORDER[target.scope] > SCOPE_ORDER[scope]:
            raise ValueError(f"scope_readonly: {refinement_id} is {target.scope}-scoped")
        state = self._load_state()
        applied: list[AppliedHarnessEdit] = []
        for item in reversed(target.applied_edits):
            if not item.applied:
                continue
            reason = f"rollback of {refinement_id}"
            if item.before is None:
                removed = state.pop(item.entry_id, None)
                edit = HarnessEdit(action="delete", kind=item.edit.kind, id=item.entry_id, reason=reason)
                applied.append(
                    AppliedHarnessEdit(
                        edit=edit,
                        entry_id=item.entry_id,
                        applied=removed is not None,
                        error="" if removed is not None else "not_found",
                        before=removed,
                    )
                )
            else:
                restored = item.before.model_copy(deep=True)
                previous = state.get(item.entry_id)
                if previous is not None and previous.outcome != "pending":
                    restored.outcome = previous.outcome
                    restored.outcome_evidence = previous.outcome_evidence
                state[item.entry_id] = restored
                action: Literal["create", "update"] = "update" if previous is not None else "create"
                edit = HarnessEdit(action=action, kind=restored.kind, id=item.entry_id, reason=reason)
                applied.append(
                    AppliedHarnessEdit(edit=edit, entry_id=item.entry_id, applied=True, before=previous, after=restored)
                )
        refinement = HarnessRefinement(
            id=f"refinement_{uuid.uuid4().hex[:8]}",
            scope=target.scope,
            summary=f"rollback of {refinement_id}",
            applied_edits=applied,
            rollback_of=refinement_id,
            created_at=self._now_iso(),
        )
        self._append_history(refinement)
        self._save_state(state)
        return refinement

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
                reference=edit.reference,
                source=source,
                created_at=now,
                updated_at=now,
            )
            state[entry_id] = entry
            return AppliedHarnessEdit(edit=edit, entry_id=entry_id, applied=True, after=entry)

        existing = state.get(edit.id)
        if existing is None:
            return AppliedHarnessEdit(edit=edit, entry_id=edit.id, applied=False, error="not_found")
        if SCOPE_ORDER[existing.scope] > SCOPE_ORDER[scope]:
            return AppliedHarnessEdit(edit=edit, entry_id=edit.id, applied=False, error="scope_readonly")
        if edit.reference is not None and edit.action == "update" and existing.kind != "procedure":
            # The edit validator only sees the edit's DECLARED kind; the target
            # entry's actual kind decides whether a reference is legal. Failing
            # here keeps this a per-edit error instead of a batch-poisoning
            # ValidationError when the mutated entry is re-validated.
            return AppliedHarnessEdit(edit=edit, entry_id=edit.id, applied=False, error="reference_requires_procedure")
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
        if edit.reference is not None:
            updated.reference = edit.reference
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
        for data in entries_raw.values():
            try:
                parsed = HarnessEntry.model_validate(data)
            except ValidationError:
                continue
            entries[parsed.id] = parsed
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
