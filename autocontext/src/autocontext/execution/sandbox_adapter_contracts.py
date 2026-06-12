"""Optional sandbox snapshot and warming adapter contracts.

The OSS package exposes these shapes so executor adapters can advertise
provider-specific startup acceleration without making local execution depend on
remote sandbox features. Hosted fleet routing, image caches, and warm pools stay
outside this contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, TypeAlias

SandboxCapabilityName: TypeAlias = Literal[
    "snapshot",
    "restore",
    "prebuild_repo_image",
    "warm",
    "resolve_tunnel_ports",
]
SandboxRequestedBootMode: TypeAlias = Literal["fresh", "restore", "repo_image", "build", "warm"]
SandboxBootMode: TypeAlias = Literal["fresh", "restored", "repo_image", "build", "warmed"]
UnsupportedSandboxCapabilityPolicy: TypeAlias = Literal["fail_closed", "degrade_to_fresh"]
SandboxCapabilityRequest: TypeAlias = Mapping[str, Any]
SandboxCapabilityResult: TypeAlias = Mapping[str, Any]
SandboxStartupPlan: TypeAlias = dict[str, Any]

SANDBOX_CAPABILITY_NAMES: tuple[SandboxCapabilityName, ...] = (
    "snapshot",
    "restore",
    "prebuild_repo_image",
    "warm",
    "resolve_tunnel_ports",
)

_BOOT_MODE_BY_REQUEST: dict[SandboxRequestedBootMode, SandboxBootMode] = {
    "fresh": "fresh",
    "restore": "restored",
    "repo_image": "repo_image",
    "build": "build",
    "warm": "warmed",
}
_REQUIRED_CAPABILITY_BY_REQUEST: dict[SandboxRequestedBootMode, SandboxCapabilityName | None] = {
    "fresh": None,
    "restore": "restore",
    "repo_image": "prebuild_repo_image",
    "build": None,
    "warm": "warm",
}


class SandboxSnapshotAdapter(Protocol):
    """Optional adapter surface for capturing reusable sandbox state."""

    def snapshot(self, request: SandboxCapabilityRequest) -> SandboxCapabilityResult:
        """Capture a provider-owned snapshot reference."""
        ...


class SandboxRestoreAdapter(Protocol):
    """Optional adapter surface for restoring reusable sandbox state."""

    def restore(self, request: SandboxCapabilityRequest) -> SandboxCapabilityResult:
        """Restore a provider-owned snapshot reference."""
        ...


class SandboxRepoImageAdapter(Protocol):
    """Optional adapter surface for repository image prebuilds."""

    def prebuild_repo_image(self, request: SandboxCapabilityRequest) -> SandboxCapabilityResult:
        """Build or resolve a provider-owned repository image reference."""
        ...


class SandboxWarmAdapter(Protocol):
    """Optional adapter surface for warm-pool preparation."""

    def warm(self, request: SandboxCapabilityRequest) -> SandboxCapabilityResult:
        """Prepare capacity or a paused sandbox for a later session."""
        ...


class SandboxTunnelPortAdapter(Protocol):
    """Optional adapter surface for tunnel endpoint resolution."""

    def resolve_tunnel_ports(self, request: SandboxCapabilityRequest) -> SandboxCapabilityResult:
        """Resolve service-port references without exposing provider secrets."""
        ...


def normalize_sandbox_adapter_capabilities(source: object | None) -> dict[SandboxCapabilityName, bool]:
    """Return explicit adapter-advertised capabilities with local-safe defaults.

    Only literal ``True`` enables a capability. Unknown provider-specific keys
    are ignored so private adapters can carry extra metadata without changing
    the portable contract.
    """

    raw = _capability_mapping(source)
    return {name: raw.get(name) is True for name in SANDBOX_CAPABILITY_NAMES}


def plan_sandbox_startup(
    *,
    session_id: str,
    requested_boot_mode: SandboxRequestedBootMode = "fresh",
    capabilities: object | None = None,
    unsupported_policy: UnsupportedSandboxCapabilityPolicy = "degrade_to_fresh",
    snapshot_id: str | None = None,
    repo_image_id: str | None = None,
    provider: str | None = None,
) -> SandboxStartupPlan:
    """Plan the OSS-visible startup branch for an optional sandbox capability."""

    del repo_image_id, provider
    _assert_requested_boot_mode(requested_boot_mode)
    _assert_unsupported_policy(unsupported_policy)
    capability = _REQUIRED_CAPABILITY_BY_REQUEST[requested_boot_mode]
    boot_mode = _BOOT_MODE_BY_REQUEST[requested_boot_mode]
    if capability is None:
        return _startup_plan(
            session_id=session_id,
            requested_boot_mode=requested_boot_mode,
            boot_mode=boot_mode,
            capability="",
            supported=True,
            degraded=False,
            terminal=False,
            unsupported_policy=unsupported_policy,
            reason="",
            lifecycle_hooks=lifecycle_hooks_for_boot_mode(boot_mode),
        )

    supported = normalize_sandbox_adapter_capabilities(capabilities)[capability]
    if supported:
        missing_ref_reason = _missing_required_ref_reason(requested_boot_mode, snapshot_id=snapshot_id)
        if missing_ref_reason:
            return _policy_guarded_plan(
                session_id=session_id,
                requested_boot_mode=requested_boot_mode,
                boot_mode=boot_mode,
                capability=capability,
                supported=True,
                unsupported_policy=unsupported_policy,
                reason=missing_ref_reason,
            )
        return _startup_plan(
            session_id=session_id,
            requested_boot_mode=requested_boot_mode,
            boot_mode=boot_mode,
            capability=capability,
            supported=True,
            degraded=False,
            terminal=False,
            unsupported_policy=unsupported_policy,
            reason="",
            lifecycle_hooks=lifecycle_hooks_for_boot_mode(boot_mode),
        )

    return _policy_guarded_plan(
        session_id=session_id,
        requested_boot_mode=requested_boot_mode,
        boot_mode=boot_mode,
        capability=capability,
        supported=False,
        unsupported_policy=unsupported_policy,
        reason=f"unsupported_{capability}",
    )


def lifecycle_hooks_for_boot_mode(boot_mode: SandboxBootMode) -> list[str]:
    """Return default setup/start hooks for a selected boot mode."""

    _assert_boot_mode(boot_mode)
    if boot_mode in {"restored", "repo_image", "warmed"}:
        return ["start"]
    return ["setup", "start"]


def _policy_guarded_plan(
    *,
    session_id: str,
    requested_boot_mode: SandboxRequestedBootMode,
    boot_mode: SandboxBootMode,
    capability: SandboxCapabilityName,
    supported: bool,
    unsupported_policy: UnsupportedSandboxCapabilityPolicy,
    reason: str,
) -> SandboxStartupPlan:
    if unsupported_policy == "fail_closed":
        return _startup_plan(
            session_id=session_id,
            requested_boot_mode=requested_boot_mode,
            boot_mode=boot_mode,
            capability=capability,
            supported=supported,
            degraded=False,
            terminal=True,
            unsupported_policy=unsupported_policy,
            reason=reason,
            lifecycle_hooks=[],
        )
    return _startup_plan(
        session_id=session_id,
        requested_boot_mode=requested_boot_mode,
        boot_mode="fresh",
        capability=capability,
        supported=supported,
        degraded=True,
        terminal=False,
        unsupported_policy=unsupported_policy,
        reason=reason,
        lifecycle_hooks=lifecycle_hooks_for_boot_mode("fresh"),
    )


def _startup_plan(
    *,
    session_id: str,
    requested_boot_mode: SandboxRequestedBootMode,
    boot_mode: SandboxBootMode,
    capability: str,
    supported: bool,
    degraded: bool,
    terminal: bool,
    unsupported_policy: UnsupportedSandboxCapabilityPolicy,
    reason: str,
    lifecycle_hooks: list[str],
) -> SandboxStartupPlan:
    return {
        "session_id": session_id,
        "requested_boot_mode": requested_boot_mode,
        "boot_mode": boot_mode,
        "capability": capability,
        "supported": supported,
        "degraded": degraded,
        "terminal": terminal,
        "unsupported_policy": unsupported_policy,
        "reason": reason,
        "lifecycle_hooks": lifecycle_hooks,
    }


def _missing_required_ref_reason(
    requested_boot_mode: SandboxRequestedBootMode,
    *,
    snapshot_id: str | None,
) -> str:
    if requested_boot_mode == "restore" and not _has_ref(snapshot_id):
        return "missing_snapshot_ref"
    return ""


def _has_ref(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _capability_mapping(source: object | None) -> Mapping[str, Any]:
    if source is None:
        return {}
    if isinstance(source, Mapping):
        return source
    raw = getattr(source, "sandbox_capabilities", None)
    if callable(raw):
        raw = raw()
    return raw if isinstance(raw, Mapping) else {}


def _assert_requested_boot_mode(value: str) -> None:
    if value not in _BOOT_MODE_BY_REQUEST:
        raise ValueError(f"Unsupported sandbox boot request: {value}")


def _assert_boot_mode(value: str) -> None:
    if value not in set(_BOOT_MODE_BY_REQUEST.values()):
        raise ValueError(f"Unsupported sandbox boot mode: {value}")


def _assert_unsupported_policy(value: str) -> None:
    if value not in {"fail_closed", "degrade_to_fresh"}:
        raise ValueError(f"Unsupported sandbox capability policy: {value}")
