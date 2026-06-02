"""AC-697 mission Python parity types (slice 1).

Mirrors ``ts/src/mission/types.ts`` (AC-410 + AC-411 data model).
Pydantic v2 frozen models with ``extra="forbid"`` so unknown keys
reject at parse time.

PR #1014 review (P2): primitive coercion is blocked per-field via
``StrictInt`` / ``StrictBool`` / ``StrictFloat`` / ``StrictStr`` so
the parity models reject the same shapes the TS Zod schemas
reject. Without these, ``MissionBudget(max_steps=True)`` would
coerce to ``1``, ``MissionBudget(max_steps="5")`` to ``5``, and
``VerifierResult(passed="no")`` to ``False``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

__all__ = [
    "MISSION_STATUSES",
    "STEP_STATUSES",
    "SUBGOAL_STATUSES",
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


# Runtime-checkable status sets so the store layer can reject
# unknown values before persisting them (PR #1014 review P2):
# `Literal` is a static-analysis hint only, not a runtime guard.
MISSION_STATUSES: frozenset[str] = frozenset(MissionStatus.__args__)  # type: ignore[attr-defined]
STEP_STATUSES: frozenset[str] = frozenset(StepStatus.__args__)  # type: ignore[attr-defined]
SUBGOAL_STATUSES: frozenset[str] = frozenset(SubgoalStatus.__args__)  # type: ignore[attr-defined]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MissionBudget(_Frozen):
    max_steps: StrictInt | None = Field(default=None, gt=0)
    max_cost_usd: StrictFloat | None = Field(default=None, gt=0)
    max_duration_minutes: StrictFloat | None = Field(default=None, gt=0)


class Mission(_Frozen):
    id: StrictStr
    name: StrictStr
    goal: StrictStr
    status: MissionStatus
    budget: MissionBudget | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: StrictStr
    updated_at: StrictStr | None = None
    completed_at: StrictStr | None = None


class MissionStep(_Frozen):
    id: StrictStr
    mission_id: StrictStr
    description: StrictStr
    status: StepStatus
    result: StrictStr | None = None
    created_at: StrictStr
    completed_at: StrictStr | None = None


class VerifierResult(_Frozen):
    passed: StrictBool
    reason: StrictStr
    suggestions: tuple[StrictStr, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionVerificationRecord(_Frozen):
    id: StrictStr
    passed: StrictBool
    reason: StrictStr
    suggestions: tuple[StrictStr, ...]
    metadata: dict[str, Any]
    created_at: StrictStr


class MissionSubgoal(_Frozen):
    id: StrictStr
    mission_id: StrictStr
    description: StrictStr
    priority: StrictInt
    status: SubgoalStatus
    created_at: StrictStr
    completed_at: StrictStr | None = None


class BudgetUsage(_Frozen):
    steps_used: StrictInt
    max_steps: StrictInt | None = None
    max_cost_usd: StrictFloat | None = None
    exhausted: StrictBool
