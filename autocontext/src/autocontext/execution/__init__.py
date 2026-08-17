from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .action_filter import ActionFilterHarness
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
    RemoteExecutionResult,
    RemoteResourceRequest,
)
from .research_workspace import (
    ResearchWorkspace,
    ResearchWorkspaceBenchmark,
    ResearchWorkspaceSnapshot,
    WorkspaceCapabilityRequest,
    WorkspaceResourceLimits,
    benchmark_research_workspace,
    grant_workspace_access,
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
    from .supervisor import ExecutionInput, ExecutionOutput, ExecutionSupervisor

_LAZY_SUPERVISOR_EXPORTS = frozenset({"ExecutionInput", "ExecutionOutput", "ExecutionSupervisor"})

__all__ = [
    "ActionFilterHarness",
    "ExecutionSupervisor",
    "ExecutionInput",
    "ExecutionOutput",
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
    "RemoteAcceleratorRequest",
    "RemoteExecutionRequest",
    "RemoteExecutionResult",
    "RemoteResourceRequest",
    "WorkspaceCapabilityRequest",
    "WorkspaceResourceLimits",
    "benchmark_research_workspace",
    "grant_workspace_access",
    "lifecycle_hooks_for_boot_mode",
    "normalize_sandbox_adapter_capabilities",
    "plan_sandbox_startup",
    "split_budget",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY_SUPERVISOR_EXPORTS:
        from .supervisor import ExecutionInput, ExecutionOutput, ExecutionSupervisor

        return {
            "ExecutionInput": ExecutionInput,
            "ExecutionOutput": ExecutionOutput,
            "ExecutionSupervisor": ExecutionSupervisor,
        }[name]
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_SUPERVISOR_EXPORTS})
