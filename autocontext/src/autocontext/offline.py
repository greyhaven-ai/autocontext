"""AC-917: ``AUTOCONTEXT_OFFLINE`` as an enforced no-egress guarantee.

The rule, scoped by who initiates:

    Offline mode means the ENGINE never initiates an outbound connection.

Scoping it that way is what makes it decidable. Control-plane sync is
engine-initiated, so it is off. An operator SSH-ing into the box is
operator-initiated, so it is simply out of scope -- the guarantee needs no
special case for inbound access, and "airgapped" does not have to mean
"unreachable".

What this module is NOT: proof. It is enforcement. The proof lives in
``tests/test_offline_mode.py``, which runs a complete generation with a
socket-level guard installed and asserts zero connection attempts, and in the
CI guard that fails the build when a new egress call site appears without one
of these checks in front of it.

Real airgap assurance comes from outside the process anyway -- a network
namespace with no route, a firewall rule, an unplugged interface. This exists so
that running autocontext inside one of those still works instead of hanging on a
call nobody expected it to make.
"""

from __future__ import annotations

import os
from typing import Any

#: Runtimes that shell out to a third-party binary which makes its OWN network
#: calls. No amount of guarding this codebase controls their sockets, so offline
#: mode refuses to start them rather than silently vouching for them.
UNAVAILABLE_OFFLINE_RUNTIMES: frozenset[str] = frozenset(
    {"claude-cli", "codex", "pi", "pi-rpc", "hermes"},
)

_TRUE = frozenset({"1", "true", "yes", "on"})


class OfflineError(RuntimeError):
    """Raised when offline mode blocks an outbound connection.

    Names what was blocked. A run that dies with "connection refused" three
    layers down teaches the operator nothing; one that says which subsystem
    tried to reach out tells them exactly which setting to change.
    """

    def __init__(self, what: str, detail: str = "") -> None:
        message = f"AUTOCONTEXT_OFFLINE is set; refusing to {what}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.what = what


def is_offline(settings: Any = None) -> bool:
    """Whether offline mode is active.

    Reads the environment directly when no settings object is supplied, because
    the guards live at egress boundaries that are not all reachable from a
    settings instance. ``AppSettings`` remains the documented surface; this is
    the same value.
    """
    if settings is not None:
        configured = getattr(settings, "offline", None)
        if isinstance(configured, bool):
            return configured
    return os.environ.get("AUTOCONTEXT_OFFLINE", "").strip().lower() in _TRUE


def require_online(what: str, *, settings: Any = None, detail: str = "") -> None:
    """Fail closed if offline mode is active.

    Call this immediately before an outbound connection, not at the top of a
    request-building function: the CI guard looks for it in the same function as
    the egress call, and a check that sits far from what it protects is one a
    later refactor moves away from.
    """
    if is_offline(settings):
        raise OfflineError(what, detail)


def runtime_is_available(runtime: str, *, settings: Any = None) -> bool:
    """Whether ``runtime`` may be started under the current mode."""
    if not is_offline(settings):
        return True
    return runtime.strip().lower() not in UNAVAILABLE_OFFLINE_RUNTIMES


def require_runtime_available(runtime: str, *, settings: Any = None) -> None:
    """Refuse a subprocess runtime whose egress this process cannot control."""
    if not runtime_is_available(runtime, settings=settings):
        raise OfflineError(
            f"start the {runtime} runtime",
            "it is a subprocess that makes its own network calls, which offline mode cannot guarantee",
        )


def check_offline_configuration(settings: Any) -> list[str]:
    """Return configuration conflicts that should stop a run before it starts.

    Offline mode and a hosted control plane are incompatible by design rather
    than by precedence. Silently letting one win would mean an operator who
    configured both gets a guarantee they did not receive, or a sync they did
    not expect -- and which of those happened would depend on load order.
    """
    if not is_offline(settings):
        return []

    conflicts: list[str] = []
    # Checked against settings that EXIST. An earlier draft guarded a
    # `control_plane_url` field, which this package does not have -- the control
    # plane is autowork's, configured on that side. A check that can never fire
    # is worse than no check: it reads like coverage.
    webhook = str(getattr(settings, "notify_webhook_url", "") or "").strip()
    if webhook:
        conflicts.append(
            f"AUTOCONTEXT_OFFLINE is set and AUTOCONTEXT_NOTIFY_WEBHOOK_URL is configured ({webhook}); "
            "posting a notification is engine-initiated egress. Unset one of them."
        )

    runtime = str(getattr(settings, "agent_provider", "") or "").strip().lower()
    if runtime in UNAVAILABLE_OFFLINE_RUNTIMES:
        conflicts.append(
            f"AUTOCONTEXT_OFFLINE is set and AUTOCONTEXT_AGENT_PROVIDER={runtime}, "
            "which shells out to a binary that makes its own network calls. "
            "Use a local endpoint (ollama, vllm, mlx) instead."
        )
    return conflicts
