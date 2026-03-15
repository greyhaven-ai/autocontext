from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SessionNotebook:
    session_id: str
    scenario_name: str
    current_objective: str = ""
    current_hypotheses: list[str] = field(default_factory=list)
    best_run_id: str | None = None
    best_generation: int | None = None
    best_score: float | None = None
    unresolved_questions: list[str] = field(default_factory=list)
    operator_observations: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    updated_at: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionNotebook:
        """Build a notebook from a persisted mapping, ignoring unknown keys."""
        return cls(
            session_id=str(data.get("session_id", "")),
            scenario_name=str(data.get("scenario_name", "")),
            current_objective=str(data.get("current_objective", "")),
            current_hypotheses=list(data.get("current_hypotheses", [])),
            best_run_id=str(data["best_run_id"]) if data.get("best_run_id") is not None else None,
            best_generation=int(data["best_generation"]) if data.get("best_generation") is not None else None,
            best_score=float(data["best_score"]) if data.get("best_score") is not None else None,
            unresolved_questions=list(data.get("unresolved_questions", [])),
            operator_observations=list(data.get("operator_observations", [])),
            follow_ups=list(data.get("follow_ups", [])),
            updated_at=str(data.get("updated_at", "")),
            created_at=str(data.get("created_at", "")),
        )
