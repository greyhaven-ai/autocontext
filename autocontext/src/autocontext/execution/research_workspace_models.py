"""Capability, lifecycle, and audit models for research workspaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

WorkspaceProfile: TypeAlias = Literal["restricted_scratch", "trusted_local", "isolated_sandbox"]
WorkspaceCapability: TypeAlias = Literal[
    "workspace_read",
    "workspace_write",
    "package_import",
    "subprocess",
    "network",
    "host_bridge",
]
WorkspaceLifecyclePolicy: TypeAlias = Literal["retain", "delete_on_close"]
CapabilityApprover: TypeAlias = Callable[["WorkspaceCapabilityRequest"], bool]
HostBridge: TypeAlias = Callable[[str, Mapping[str, Any]], Any]

_CAPABILITIES: frozenset[str] = frozenset(
    {"workspace_read", "workspace_write", "package_import", "subprocess", "network", "host_bridge"}
)


@dataclass(frozen=True, slots=True)
class WorkspaceResourceLimits:
    timeout_seconds: float = 10.0
    subprocess_timeout_seconds: float = 10.0
    max_stdout_chars: int = 8192
    max_file_bytes: int = 8 * 1024 * 1024
    max_network_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.subprocess_timeout_seconds <= 0:
            raise ValueError("workspace timeouts must be positive")
        if self.max_stdout_chars <= 0 or self.max_file_bytes <= 0 or self.max_network_bytes <= 0:
            raise ValueError("workspace byte limits must be positive")


@dataclass(frozen=True, slots=True)
class WorkspaceCapabilityRequest:
    workspace_id: str
    profile: WorkspaceProfile = "restricted_scratch"
    requested_capabilities: frozenset[WorkspaceCapability] = frozenset()
    allowed_imports: frozenset[str] = frozenset()
    allowed_commands: frozenset[str] = frozenset()
    allowed_network_hosts: frozenset[str] = frozenset()
    limits: WorkspaceResourceLimits = field(default_factory=WorkspaceResourceLimits)
    lifecycle: WorkspaceLifecyclePolicy = "retain"
    approval_context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        unknown = set(self.requested_capabilities) - _CAPABILITIES
        if unknown:
            raise ValueError(f"unknown workspace capabilities: {sorted(unknown)}")
        if self.profile == "restricted_scratch" and self.requested_capabilities:
            raise ValueError("restricted_scratch does not accept elevated capabilities")


@dataclass(frozen=True, slots=True)
class WorkspaceGrant:
    workspace_id: str
    profile: WorkspaceProfile
    requested_capabilities: frozenset[WorkspaceCapability]
    granted_capabilities: frozenset[WorkspaceCapability]
    denied_capabilities: frozenset[WorkspaceCapability]
    limits: WorkspaceResourceLimits
    approval_context: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class WorkspaceAuditEvent:
    sequence: int
    timestamp: float
    workspace_id: str
    profile: WorkspaceProfile
    action: str
    outcome: str
    capabilities: tuple[WorkspaceCapability, ...]
    resource: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ResearchWorkspaceSnapshot:
    workspace_id: str
    variables: Mapping[str, Any]
    helper_sources: tuple[str, ...]
    files: Mapping[str, bytes]
    skipped_variables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceCleanupResult:
    outcome: Literal["retained", "deleted", "already_closed", "error"]
    workspace_root: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ResearchWorkspaceBenchmark:
    restricted_task_quality: float
    capable_task_quality: float
    restricted_wall_seconds: float
    capable_wall_seconds: float
    capable_prompt_chars: tuple[int, ...]
    restricted_cleanup: str
    capable_cleanup: str


__all__ = [
    "CapabilityApprover",
    "HostBridge",
    "ResearchWorkspaceBenchmark",
    "ResearchWorkspaceSnapshot",
    "WorkspaceAuditEvent",
    "WorkspaceCapability",
    "WorkspaceCapabilityRequest",
    "WorkspaceCleanupResult",
    "WorkspaceGrant",
    "WorkspaceLifecyclePolicy",
    "WorkspaceProfile",
    "WorkspaceResourceLimits",
]
