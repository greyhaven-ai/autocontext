from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .action_filter import ActionFilterHarness
from .campaign_scheduler import (
    CallableCampaignWorker,
    CampaignBatchWorker,
    CampaignJobRequest,
    CampaignJobResult,
    CampaignScheduler,
    CampaignSchedulerEventStore,
    CancellableCampaignWorker,
    RemoteCampaignWorker,
    SchedulerBudget,
    SchedulerResources,
    StaleCampaignSchedulerError,
    WorkerDescriptor,
)
from .docker_research_sandbox import DockerResearchSandboxBackend, SecretGrantResolver
from .external_eval_outbox import (
    ExternalEvalLedgerOutbox,
    ExternalEvalOutboxConflictError,
    ExternalEvalOutboxPendingError,
    ExternalEvalOutboxStatus,
    ExternalEvalSinkDeliveryPendingError,
    ExternalEvalSinkDeliveryReservation,
)
from .phased_execution import (
    PhaseBudget,
    PhasedExecutionPlan,
    PhasedExecutionResult,
    PhasedRunner,
    PhaseResult,
    split_budget,
)
from .remote_execution import (
    RemoteAcceleratorRequest,
    RemoteExecutionRequest,
    RemoteExecutionRequirements,
    RemoteExecutionResult,
    RemoteProviderCapabilities,
    RemoteResolvedEnvironment,
    RemoteResourceRequest,
)
from .remote_failure import RemoteExecutionAccountingError, RemoteExecutionError, RemoteExecutionFailure
from .research_workspace_models import (
    ResearchSandboxBackend,
    ResearchSandboxExecutionRequest,
    ResearchSandboxExecutionResult,
    SandboxBackendCapabilities,
    SandboxBackendCleanupResult,
    WorkspaceSecretGrant,
)
from .sandbox_adapter_contracts import (
    SANDBOX_CAPABILITY_NAMES,
    SandboxBootMode,
    SandboxCapabilityName,
    SandboxRepoImageAdapter,
    SandboxRequestedBootMode,
    SandboxRestoreAdapter,
    SandboxSnapshotAdapter,
    SandboxStartupPlan,
    SandboxTunnelPortAdapter,
    SandboxWarmAdapter,
    UnsupportedSandboxCapabilityPolicy,
    lifecycle_hooks_for_boot_mode,
    normalize_sandbox_adapter_capabilities,
    plan_sandbox_startup,
)
from .task_queue_store import TaskQueueEnqueueStore, TaskQueueStore

if TYPE_CHECKING:
    from .research_workspace import (
        ResearchWorkspace,
        ResearchWorkspaceBenchmark,
        ResearchWorkspaceSnapshot,
        WorkspaceCapabilityRequest,
        WorkspaceResourceLimits,
        benchmark_research_workspace,
        grant_workspace_access,
    )
    from .supervisor import ExecutionInput, ExecutionOutput, ExecutionSupervisor

_LAZY_RESEARCH_WORKSPACE_EXPORTS = frozenset(
    {
        "ResearchWorkspace",
        "ResearchWorkspaceBenchmark",
        "ResearchWorkspaceSnapshot",
        "WorkspaceCapabilityRequest",
        "WorkspaceResourceLimits",
        "benchmark_research_workspace",
        "grant_workspace_access",
    }
)
_LAZY_SUPERVISOR_EXPORTS = frozenset({"ExecutionInput", "ExecutionOutput", "ExecutionSupervisor"})

__all__ = [
    "ActionFilterHarness",
    "CallableCampaignWorker",
    "CampaignBatchWorker",
    "CancellableCampaignWorker",
    "CampaignJobRequest",
    "CampaignJobResult",
    "CampaignScheduler",
    "CampaignSchedulerEventStore",
    "ExecutionSupervisor",
    "ExecutionInput",
    "ExecutionOutput",
    "ExternalEvalLedgerOutbox",
    "ExternalEvalOutboxConflictError",
    "ExternalEvalOutboxPendingError",
    "ExternalEvalOutboxStatus",
    "ExternalEvalSinkDeliveryPendingError",
    "ExternalEvalSinkDeliveryReservation",
    "DockerResearchSandboxBackend",
    "SANDBOX_CAPABILITY_NAMES",
    "SandboxBootMode",
    "SandboxCapabilityName",
    "SandboxRepoImageAdapter",
    "SandboxRequestedBootMode",
    "SandboxRestoreAdapter",
    "SandboxSnapshotAdapter",
    "SandboxStartupPlan",
    "SandboxTunnelPortAdapter",
    "SandboxWarmAdapter",
    "UnsupportedSandboxCapabilityPolicy",
    "TaskQueueEnqueueStore",
    "TaskQueueStore",
    "PhaseBudget",
    "PhaseResult",
    "PhasedExecutionPlan",
    "PhasedExecutionResult",
    "PhasedRunner",
    "ResearchWorkspace",
    "ResearchWorkspaceBenchmark",
    "ResearchWorkspaceSnapshot",
    "ResearchSandboxBackend",
    "ResearchSandboxExecutionRequest",
    "ResearchSandboxExecutionResult",
    "RemoteAcceleratorRequest",
    "RemoteCampaignWorker",
    "RemoteExecutionAccountingError",
    "RemoteExecutionError",
    "RemoteExecutionRequirements",
    "RemoteExecutionFailure",
    "RemoteExecutionRequest",
    "RemoteExecutionResult",
    "RemoteProviderCapabilities",
    "RemoteResolvedEnvironment",
    "RemoteResourceRequest",
    "SchedulerBudget",
    "SchedulerResources",
    "SecretGrantResolver",
    "SandboxBackendCapabilities",
    "SandboxBackendCleanupResult",
    "StaleCampaignSchedulerError",
    "WorkspaceCapabilityRequest",
    "WorkspaceResourceLimits",
    "WorkspaceSecretGrant",
    "WorkerDescriptor",
    "benchmark_research_workspace",
    "grant_workspace_access",
    "lifecycle_hooks_for_boot_mode",
    "normalize_sandbox_adapter_capabilities",
    "plan_sandbox_startup",
    "split_budget",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY_RESEARCH_WORKSPACE_EXPORTS:
        from .research_workspace import (
            ResearchWorkspace,
            ResearchWorkspaceBenchmark,
            ResearchWorkspaceSnapshot,
            WorkspaceCapabilityRequest,
            WorkspaceResourceLimits,
            benchmark_research_workspace,
            grant_workspace_access,
        )

        return {
            "ResearchWorkspace": ResearchWorkspace,
            "ResearchWorkspaceBenchmark": ResearchWorkspaceBenchmark,
            "ResearchWorkspaceSnapshot": ResearchWorkspaceSnapshot,
            "WorkspaceCapabilityRequest": WorkspaceCapabilityRequest,
            "WorkspaceResourceLimits": WorkspaceResourceLimits,
            "benchmark_research_workspace": benchmark_research_workspace,
            "grant_workspace_access": grant_workspace_access,
        }[name]
    if name in _LAZY_SUPERVISOR_EXPORTS:
        from .supervisor import ExecutionInput, ExecutionOutput, ExecutionSupervisor

        return {
            "ExecutionInput": ExecutionInput,
            "ExecutionOutput": ExecutionOutput,
            "ExecutionSupervisor": ExecutionSupervisor,
        }[name]
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_RESEARCH_WORKSPACE_EXPORTS, *_LAZY_SUPERVISOR_EXPORTS})
