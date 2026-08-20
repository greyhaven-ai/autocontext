"""Capability, lifecycle, and audit models for research workspaces."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias

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
WorkspaceCredentialBroker: TypeAlias = Callable[["WorkspaceSecretGrant", str, Mapping[str, Any]], Any]

_CAPABILITIES: frozenset[str] = frozenset(
    {"workspace_read", "workspace_write", "package_import", "subprocess", "network", "host_bridge"}
)


@dataclass(frozen=True, slots=True)
class WorkspaceResourceLimits:
    timeout_seconds: float = 10.0
    subprocess_timeout_seconds: float = 10.0
    max_stdout_chars: int = 8192
    max_file_bytes: int = 8 * 1024 * 1024
    max_workspace_bytes: int = 64 * 1024 * 1024
    max_workspace_inodes: int = 4096
    max_network_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        timeouts = (self.timeout_seconds, self.subprocess_timeout_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in timeouts):
            raise ValueError("workspace timeouts must be positive and finite")
        if (
            self.max_stdout_chars <= 0
            or self.max_file_bytes <= 0
            or self.max_workspace_bytes <= 0
            or self.max_workspace_inodes <= 0
            or self.max_network_bytes <= 0
        ):
            raise ValueError("workspace byte and inode limits must be positive")


@dataclass(frozen=True, slots=True)
class WorkspaceSecretGrant:
    """Opaque, expiring reference usable only through a host-side broker."""

    name: str
    grant_id: str
    expires_at: float
    env_var: str = ""
    allowed_operations: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.grant_id.strip():
            raise ValueError("workspace secret grant name and id must be non-empty")
        if not math.isfinite(self.expires_at):
            raise ValueError("workspace secret grant expiry must be finite")
        if any(not operation.strip() for operation in self.allowed_operations):
            raise ValueError("workspace credential broker operations must be non-empty")


@dataclass(frozen=True, slots=True)
class WorkspaceCapabilityRequest:
    workspace_id: str
    profile: WorkspaceProfile = "restricted_scratch"
    requested_capabilities: frozenset[WorkspaceCapability] = frozenset()
    allowed_imports: frozenset[str] = frozenset()
    allowed_commands: frozenset[str] = frozenset()
    allowed_network_hosts: frozenset[str] = frozenset()
    secret_grants: tuple[WorkspaceSecretGrant, ...] = ()
    limits: WorkspaceResourceLimits = field(default_factory=WorkspaceResourceLimits)
    lifecycle: WorkspaceLifecyclePolicy = "retain"
    approval_context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        if len(self.workspace_id.encode("utf-8")) > 128:
            raise ValueError("workspace_id must be at most 128 UTF-8 bytes")
        if self.profile not in {"restricted_scratch", "trusted_local", "isolated_sandbox"}:
            raise ValueError(f"unknown workspace profile: {self.profile}")
        if self.lifecycle not in {"retain", "delete_on_close"}:
            raise ValueError(f"unknown workspace lifecycle: {self.lifecycle}")
        unknown = set(self.requested_capabilities) - _CAPABILITIES
        if unknown:
            raise ValueError(f"unknown workspace capabilities: {sorted(unknown)}")
        if self.profile == "restricted_scratch" and self.requested_capabilities:
            raise ValueError("restricted_scratch does not accept elevated capabilities")
        if self.profile == "isolated_sandbox" and {"package_import", "subprocess"}.issubset(
            self.requested_capabilities
        ):
            raise ValueError(
                "isolated_sandbox cannot combine package_import and subprocess; imported callables bypass command allowlists"
            )
        if self.secret_grants and self.profile != "isolated_sandbox":
            raise ValueError("workspace secret grants require the isolated_sandbox profile")


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
class SandboxBackendCapabilities:
    """Security properties an isolated workspace backend must attest."""

    backend_name: str
    os_isolation: bool = False
    workspace_mounts: bool = False
    network_policy: bool = False
    process_limits: bool = False
    environment_scrubbing: bool = False
    secret_grants: bool = False
    transactional_files: bool = False
    terminable_execution: bool = False
    cleanup_verification: bool = False

    def __post_init__(self) -> None:
        if not self.backend_name.strip():
            raise ValueError("sandbox backend_name must be non-empty")


@dataclass(frozen=True, slots=True)
class ResearchSandboxExecutionRequest:
    workspace_id: str
    sequence: int
    code: str
    variables: Mapping[str, Any]
    helper_sources: tuple[str, ...]
    files: Mapping[str, bytes]
    granted_capabilities: frozenset[WorkspaceCapability]
    allowed_imports: frozenset[str]
    allowed_commands: frozenset[str]
    allowed_network_hosts: frozenset[str]
    secret_grants: tuple[WorkspaceSecretGrant, ...]
    limits: WorkspaceResourceLimits


@dataclass(frozen=True, slots=True)
class ResearchSandboxExecutionResult:
    stdout: str = ""
    error: str | None = None
    answer: Mapping[str, Any] = field(default_factory=dict)
    variables: Mapping[str, Any] = field(default_factory=dict)
    helper_sources: tuple[str, ...] = ()
    files: Mapping[str, bytes] = field(default_factory=dict)
    session_id: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SandboxBackendCleanupResult:
    succeeded: bool
    detail: str = ""


class ResearchSandboxBackend(Protocol):
    """Adapter for an OS/VM/container isolation boundary."""

    def capabilities(self) -> SandboxBackendCapabilities:
        """Return security properties enforced below candidate code."""
        ...

    def execute(self, request: ResearchSandboxExecutionRequest) -> ResearchSandboxExecutionResult:
        """Execute one transactional workspace generation."""
        ...

    def cleanup(self, workspace_id: str) -> SandboxBackendCleanupResult:
        """Terminate and verify deletion of every resource for a workspace."""
        ...


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
    "ResearchSandboxBackend",
    "ResearchSandboxExecutionRequest",
    "ResearchSandboxExecutionResult",
    "ResearchWorkspaceSnapshot",
    "SandboxBackendCapabilities",
    "SandboxBackendCleanupResult",
    "WorkspaceAuditEvent",
    "WorkspaceCapability",
    "WorkspaceCapabilityRequest",
    "WorkspaceCleanupResult",
    "WorkspaceCredentialBroker",
    "WorkspaceGrant",
    "WorkspaceLifecyclePolicy",
    "WorkspaceProfile",
    "WorkspaceResourceLimits",
    "WorkspaceSecretGrant",
]
