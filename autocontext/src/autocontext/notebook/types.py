from __future__ import annotations

from dataclasses import dataclass, field


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
