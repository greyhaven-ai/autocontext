"""Typed harness entries with scoped CRUD edits and rollback (AC-898).

Lessons, policies, procedures, and delegation specs persisted as typed,
scoped, versioned entries instead of accumulated prose. Every mutation is
a CRUD edit recorded to an append-only refinement history with
before/after snapshots, so any refinement can be rolled back. Modeled on
prime-agent's continual-harness refinement store; adds outcome marking so
verifier-scored runs can confirm or refute an entry's expected outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

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
