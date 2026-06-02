"""AC-697 mission Python parity surface (slices 1 + 2).

Mirrors ``ts/src/mission/`` step-by-step.

- slice 1: SQLite store + Pydantic models (``store``, ``types``).
- slice 2: MissionManager + verifiers + planner + lifecycle helpers
  (``manager``, ``verifiers``, ``planner``, ``lifecycle``,
  ``verification``, ``events``).
- slice 3: control-plane workflows (checkpoint, status payloads).
- slice 4 / 5: ``autoctx mission`` CLI.
"""

from autocontext.mission.events import (
    MissionCreatedEvent,
    MissionEventEmitter,
    MissionStatusChangedEvent,
    MissionStepEvent,
    MissionVerifiedEvent,
)
from autocontext.mission.lifecycle import (
    MissionStatusTransition,
    build_verifier_error_result,
    can_transition_mission_status,
    derive_mission_status_from_verifier_result,
    resolve_mission_status_transition,
)
from autocontext.mission.manager import MissionManager, MissionVerifierCallable
from autocontext.mission.planner import (
    LLMCompletion,
    LLMCompletionRequest,
    LLMProvider,
    MissionPlanner,
    PlanNextStepOpts,
    PlanResult,
    StepPlan,
    SubgoalPlan,
    VerifierFeedback,
)
from autocontext.mission.store import MissionStore
from autocontext.mission.types import (
    BudgetUsage,
    Mission,
    MissionBudget,
    MissionStatus,
    MissionStep,
    MissionSubgoal,
    MissionVerificationRecord,
    StepStatus,
    SubgoalStatus,
    VerifierResult,
)
from autocontext.mission.verification import (
    MissionVerificationOutcome,
    build_missing_verifier_outcome,
    resolve_mission_verification_error_outcome,
    resolve_mission_verification_outcome,
)
from autocontext.mission.verifiers import (
    CodeMissionSpec,
    CommandVerifier,
    CompositeVerifier,
    Verifier,
    attach_code_mission_verifier,
    create_code_mission,
    rehydrate_mission_verifier,
)

__all__ = [
    "BudgetUsage",
    "CodeMissionSpec",
    "CommandVerifier",
    "CompositeVerifier",
    "LLMCompletion",
    "LLMCompletionRequest",
    "LLMProvider",
    "Mission",
    "MissionBudget",
    "MissionCreatedEvent",
    "MissionEventEmitter",
    "MissionManager",
    "MissionPlanner",
    "MissionStatus",
    "MissionStatusChangedEvent",
    "MissionStatusTransition",
    "MissionStep",
    "MissionStepEvent",
    "MissionStore",
    "MissionSubgoal",
    "MissionVerificationOutcome",
    "MissionVerificationRecord",
    "MissionVerifiedEvent",
    "MissionVerifierCallable",
    "PlanNextStepOpts",
    "PlanResult",
    "StepPlan",
    "StepStatus",
    "SubgoalPlan",
    "SubgoalStatus",
    "Verifier",
    "VerifierFeedback",
    "VerifierResult",
    "attach_code_mission_verifier",
    "build_missing_verifier_outcome",
    "build_verifier_error_result",
    "can_transition_mission_status",
    "create_code_mission",
    "derive_mission_status_from_verifier_result",
    "rehydrate_mission_verifier",
    "resolve_mission_status_transition",
    "resolve_mission_verification_error_outcome",
    "resolve_mission_verification_outcome",
]
