"""AC-697 mission Python parity surface (slices 1 + 2 + 3).

Mirrors ``ts/src/mission/`` step-by-step.

- slice 1: SQLite store + Pydantic models (``store``, ``types``).
- slice 2: MissionManager + verifiers + planner + lifecycle helpers
  (``manager``, ``verifiers``, ``planner``, ``lifecycle``,
  ``verification``, ``events``).
- slice 3: control-plane workflows (``checkpoint``, ``executor``,
  ``control_plane``).
- slice 4 / 5: ``autoctx mission`` CLI.
"""

from autocontext.mission.checkpoint import (
    CHECKPOINT_VERSION,
    load_checkpoint,
    save_checkpoint,
)
from autocontext.mission.control_plane import (
    build_fallback_verifier,
    build_mission_artifacts_payload,
    build_mission_result_payload,
    build_mission_status_payload,
    mission_checkpoint_dir,
    require_mission,
    run_mission_loop,
    write_mission_checkpoint,
)
from autocontext.mission.events import (
    MissionCreatedEvent,
    MissionEventEmitter,
    MissionStatusChangedEvent,
    MissionStepEvent,
    MissionVerifiedEvent,
)
from autocontext.mission.executor import (
    RunStepResult,
    RunUntilDoneResult,
    StepExecutor,
    StepResult,
    run_step,
    run_until_done,
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
    "CHECKPOINT_VERSION",
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
    "RunStepResult",
    "RunUntilDoneResult",
    "StepExecutor",
    "StepPlan",
    "StepResult",
    "StepStatus",
    "SubgoalPlan",
    "SubgoalStatus",
    "Verifier",
    "VerifierFeedback",
    "VerifierResult",
    "attach_code_mission_verifier",
    "build_fallback_verifier",
    "build_mission_artifacts_payload",
    "build_mission_result_payload",
    "build_mission_status_payload",
    "build_missing_verifier_outcome",
    "build_verifier_error_result",
    "can_transition_mission_status",
    "create_code_mission",
    "derive_mission_status_from_verifier_result",
    "load_checkpoint",
    "mission_checkpoint_dir",
    "rehydrate_mission_verifier",
    "require_mission",
    "resolve_mission_status_transition",
    "resolve_mission_verification_error_outcome",
    "resolve_mission_verification_outcome",
    "run_mission_loop",
    "run_step",
    "run_until_done",
    "save_checkpoint",
    "write_mission_checkpoint",
]
