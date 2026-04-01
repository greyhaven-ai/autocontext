from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionNotebook(BaseModel):
    session_id: str
    scenario_name: str
    current_objective: str = ""
    current_hypotheses: list[str] = Field(default_factory=list)
    best_run_id: str | None = None
    best_generation: int | None = None
    best_score: float | None = None
    unresolved_questions: list[str] = Field(default_factory=list)
    operator_observations: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    updated_at: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionNotebook:
        return cls.model_validate(data)
