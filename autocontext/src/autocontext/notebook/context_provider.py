"""Role-specific notebook context provider and effective-context preview (AC-261).

Wires session notebook state into runtime prompts as first-class input.
Each agent role receives only the notebook fields relevant to its task,
and guardrails prevent stale or contradictory context from silently
dominating run-local evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from autocontext.notebook.types import SessionNotebook

# Role → notebook fields mapping.  Each role sees only the fields
# that meaningfully inform its task.
ROLE_NOTEBOOK_FIELDS: dict[str, list[str]] = {
    "competitor": ["current_objective", "current_hypotheses", "follow_ups"],
    "analyst": ["current_objective", "unresolved_questions", "operator_observations"],
    "coach": ["current_objective", "follow_ups", "operator_observations"],
    "architect": ["current_hypotheses", "unresolved_questions"],
}

# Human-readable section headers for each notebook field.
_FIELD_HEADERS: dict[str, str] = {
    "current_objective": "Current Objective",
    "current_hypotheses": "Active Hypotheses",
    "unresolved_questions": "Unresolved Questions",
    "operator_observations": "Operator Observations",
    "follow_ups": "Follow-ups",
}


@dataclass(slots=True)
class NotebookContextWarning:
    """Warning about stale or contradictory notebook context."""

    field: str
    warning_type: str  # stale_score, stale_context
    description: str


class EffectiveContextPreview(BaseModel):
    """Preview of notebook-derived context that will be injected at runtime."""

    session_id: str
    role_contexts: dict[str, str]
    warnings: list[NotebookContextWarning]
    notebook_empty: bool
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EffectiveContextPreview:
        return cls.model_validate(data)


class NotebookContextProvider:
    """Produces role-specific notebook context with guardrails."""

    def for_role(
        self,
        notebook: SessionNotebook,
        role: str,
    ) -> str:
        """Return role-specific notebook context as markdown.

        Returns empty string if the role is unknown, the notebook is empty,
        or none of the role's fields have content.
        """
        allowed_fields = ROLE_NOTEBOOK_FIELDS.get(role)
        if allowed_fields is None:
            return ""

        sections: list[str] = []
        for field_name in allowed_fields:
            value = getattr(notebook, field_name, None)
            if not value:
                continue

            header = _FIELD_HEADERS.get(field_name, field_name)
            if isinstance(value, list):
                items = "\n".join(f"- {item}" for item in value)
                sections.append(f"### {header}\n{items}")
            else:
                sections.append(f"### {header}\n{value}")

        if not sections:
            return ""

        return f"## Session Notebook ({notebook.session_id})\n\n" + "\n\n".join(sections)

    def check_warnings(
        self,
        notebook: SessionNotebook,
        current_best_score: float | None = None,
    ) -> list[NotebookContextWarning]:
        """Check for stale or contradictory notebook context."""
        warnings: list[NotebookContextWarning] = []

        # Stale score: notebook's best_score is lower than current run's best
        if (
            notebook.best_score is not None
            and current_best_score is not None
            and current_best_score > notebook.best_score
        ):
            warnings.append(NotebookContextWarning(
                field="best_score",
                warning_type="stale_score",
                description=(
                    f"Notebook best score {notebook.best_score} is below "
                    f"current run best {current_best_score}"
                ),
            ))

        return warnings

    def build_effective_preview(
        self,
        notebook: SessionNotebook,
        current_best_score: float | None = None,
    ) -> EffectiveContextPreview:
        """Build effective context preview for all roles."""
        now = datetime.now(UTC).isoformat()

        role_contexts: dict[str, str] = {}
        for role in ROLE_NOTEBOOK_FIELDS:
            ctx = self.for_role(notebook, role)
            if ctx:
                role_contexts[role] = ctx

        warnings = self.check_warnings(notebook, current_best_score=current_best_score)

        notebook_empty = not any(
            getattr(notebook, f, None)
            for fields in ROLE_NOTEBOOK_FIELDS.values()
            for f in fields
        )

        return EffectiveContextPreview(
            session_id=notebook.session_id,
            role_contexts=role_contexts,
            warnings=warnings,
            notebook_empty=notebook_empty,
            created_at=now,
        )
