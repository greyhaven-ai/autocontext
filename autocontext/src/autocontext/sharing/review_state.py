"""Trace-exchange review state machine (Python consumer).

Faithful port of ``review-state.ts`` from the website source of truth. Kept in
parity by the shared toxic-fixture / state-machine contract tests. No
transition path reaches ``approved_public`` without passing quarantine,
scanning, and human review; ``rejected`` and ``takedown`` are terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from autocontext.sharing.safeguards import ReviewState

ReviewActor = Literal["automated", "uploader", "reviewer", "system"]

INITIAL_STATE: ReviewState = "uploaded"


@dataclass(slots=True, frozen=True)
class ReviewStateMeta:
    label: str
    description: str
    actor: ReviewActor
    terminal: bool


REVIEW_STATE_META: dict[ReviewState, ReviewStateMeta] = {
    "uploaded": ReviewStateMeta(
        "uploaded",
        "bundle received from an authenticated account. nothing is public or model-readable yet.",
        "automated",
        False,
    ),
    "quarantined": ReviewStateMeta(
        "quarantined",
        "stored in the private quarantine bucket. only the pipeline and reviewers can read it.",
        "automated",
        False,
    ),
    "scanning": ReviewStateMeta(
        "scanning",
        "secret, PII, encoded-payload, and malicious-code scanners run over every file.",
        "automated",
        False,
    ),
    "needs_user_redaction": ReviewStateMeta(
        "needs user redaction",
        "scanners found redactable spans. the uploader confirms the manifest and resubmits.",
        "uploader",
        False,
    ),
    "needs_human_review": ReviewStateMeta(
        "needs human review",
        "clean or uncertain scans still require a reviewer before anything becomes public.",
        "reviewer",
        False,
    ),
    "approved_private": ReviewStateMeta(
        "approved private",
        "visible to the uploader's org only. public release needs another review.",
        "reviewer",
        False,
    ),
    "approved_public": ReviewStateMeta(
        "approved public",
        "served publicly and eligible for model-readable collections, with a takedown path.",
        "reviewer",
        False,
    ),
    "rejected": ReviewStateMeta(
        "rejected",
        "blocked by scanners or review. the bundle never leaves quarantine.",
        "system",
        True,
    ),
    "takedown": ReviewStateMeta(
        "takedown",
        "removed after publication on owner or reviewer request.",
        "system",
        True,
    ),
}

REVIEW_TRANSITIONS: dict[ReviewState, list[ReviewState]] = {
    "uploaded": ["quarantined"],
    "quarantined": ["scanning"],
    "scanning": ["needs_user_redaction", "needs_human_review", "rejected"],
    "needs_user_redaction": ["scanning", "rejected"],
    "needs_human_review": ["approved_private", "approved_public", "rejected"],
    "approved_private": ["needs_human_review", "takedown"],
    "approved_public": ["takedown"],
    "rejected": [],
    "takedown": [],
}


def get_next_review_states(state: ReviewState) -> list[ReviewState]:
    return REVIEW_TRANSITIONS[state]


def can_transition_review(from_state: ReviewState, to_state: ReviewState) -> bool:
    return to_state in REVIEW_TRANSITIONS[from_state]


def get_review_state_meta(state: ReviewState) -> ReviewStateMeta:
    return REVIEW_STATE_META[state]
