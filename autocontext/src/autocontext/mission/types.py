"""AC-697 mission Python parity types (slice 1).

Mirrors ``ts/src/mission/types.ts`` (AC-410 + AC-411 data model).
Pydantic v2 frozen models with ``extra="forbid"`` so unknown keys
reject at parse time.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BudgetUsage",
    "Mission",
    "MissionBudget",
    "MissionStatus",
    "MissionStep",
    "MissionSubgoal",
    "MissionVerificationRecord",
    "StepStatus",
    "SubgoalStatus",
    "VerifierResult",
]


MissionStatus = Literal[
    "active",
    "paused",
    "completed",
    "failed",
    "canceled",
    "blocked",
    "budget_exhausted",
    "verifier_failed",
]


StepStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
    "blocked",
]


SubgoalStatus = Literal[
    "pending",
    "active",
    "completed",
    "failed",
    "skipped",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MissionBudget(_Frozen):
    max_steps: int | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, gt=0)
    max_duration_minutes: float | None = Field(default=None, gt=0)


class Mission(_Frozen):
    id: str
    name: str
    goal: str
    status: MissionStatus
    budget: MissionBudget | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str | None = None
    completed_at: str | None = None


class MissionStep(_Frozen):
    id: str
    mission_id: str
    description: str
    status: StepStatus
    result: str | None = None
    created_at: str
    completed_at: str | None = None


class VerifierResult(_Frozen):
    passed: bool
    reason: str
    suggestions: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionVerificationRecord(_Frozen):
    id: str
    passed: bool
    reason: str
    suggestions: tuple[str, ...]
    metadata: dict[str, Any]
    created_at: str


class MissionSubgoal(_Frozen):
    id: str
    mission_id: str
    description: str
    priority: int
    status: SubgoalStatus
    created_at: str
    completed_at: str | None = None


class BudgetUsage(_Frozen):
    steps_used: int
    max_steps: int | None = None
    max_cost_usd: float | None = None
    exhausted: bool
