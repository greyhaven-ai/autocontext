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
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

#: Runtimes that shell out to a third-party binary which makes its OWN network
#: calls. No amount of guarding this codebase controls their sockets, so offline
#: mode refuses to start them rather than silently vouching for them.
UNAVAILABLE_OFFLINE_RUNTIMES: frozenset[str] = frozenset(
    {
        "agent_sdk",
        "claude-cli",
        "codex",
        "hermes",
        "openclaw-cli",
        "openclaw-factory",
        "pi",
        "pi-rpc",
    },
)

# Keep this exactly aligned with the string spellings Pydantic accepts for a
# boolean field. The egress guards often do not have an AppSettings instance,
# so parsing the environment here must not disagree with load_settings().
_TRUE = frozenset({"1", "true", "yes", "on", "y", "t"})

_HTTP_PROVIDER_DEFAULTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openai-compatible": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
}
_ROLE_PROVIDER_FIELDS = (
    ("competitor", "competitor_provider", "competitor_base_url"),
    ("architect", "architect_provider", "architect_base_url"),
    ("analyst", "analyst_provider", "analyst_base_url"),
    ("coach", "coach_provider", "coach_base_url"),
)


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


def is_local_endpoint(endpoint: str) -> bool:
    """Whether an endpoint is unambiguously confined to this host.

    Hostnames other than the exact ``localhost`` name are deliberately not
    resolved here: DNS resolution is itself egress and a name that happens to
    resolve to loopback now can be rebound later. Literal loopback addresses
    cover the rest of the local-server cases without that ambiguity.
    """
    try:
        host = urlsplit(endpoint).hostname
    except ValueError:
        return False
    if host is None:
        return False
    normalized = host.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def require_endpoint_available(
    what: str,
    endpoint: str,
    *,
    settings: Any = None,
) -> None:
    """Allow a literal loopback endpoint offline; block every other endpoint."""
    if is_offline(settings) and not is_local_endpoint(endpoint):
        raise OfflineError(what, endpoint)


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

    agent_provider = _setting(settings, "agent_provider")
    agent_base_url = _setting(settings, "agent_base_url") or _setting(settings, "judge_base_url")
    conflict = _provider_conflict(
        "AUTOCONTEXT_AGENT_PROVIDER",
        agent_provider,
        agent_base_url,
        settings,
    )
    if conflict:
        conflicts.append(conflict)

    for role, provider_field, base_url_field in _ROLE_PROVIDER_FIELDS:
        provider = _setting(settings, provider_field)
        base_url = _setting(settings, base_url_field)
        if not provider and not base_url:
            continue
        conflict = _provider_conflict(
            f"AUTOCONTEXT_{role.upper()}_PROVIDER",
            provider or agent_provider,
            base_url or agent_base_url,
            settings,
        )
        if conflict:
            conflicts.append(conflict)

    judge_provider = _setting(settings, "judge_provider")
    if judge_provider == "auto":
        judge_provider = _resolve_auto_judge_provider(settings)
    conflict = _provider_conflict(
        "AUTOCONTEXT_JUDGE_PROVIDER",
        judge_provider,
        _setting(settings, "judge_base_url"),
        settings,
    )
    if conflict:
        conflicts.append(conflict)

    if bool(getattr(settings, "consultation_enabled", False)):
        conflict = _provider_conflict(
            "AUTOCONTEXT_CONSULTATION_PROVIDER",
            _setting(settings, "consultation_provider"),
            _setting(settings, "consultation_base_url"),
            settings,
        )
        if conflict:
            conflicts.append(conflict)

    executor_mode = _setting(settings, "executor_mode")
    if executor_mode in {"primeintellect", "ssh"}:
        conflicts.append(
            f"AUTOCONTEXT_OFFLINE is set and AUTOCONTEXT_EXECUTOR_MODE={executor_mode}; "
            "the executor initiates a remote connection. Use local or monty instead."
        )

    if bool(getattr(settings, "blob_store_enabled", False)) and _setting(
        settings,
        "blob_store_backend",
    ) == "hf_bucket":
        conflicts.append(
            "AUTOCONTEXT_OFFLINE is set and AUTOCONTEXT_BLOB_STORE_BACKEND=hf_bucket; "
            "Hugging Face artifact synchronization is engine-initiated egress. Use local storage instead."
        )

    if _setting(settings, "openclaw_distill_sidecar_command") or _setting(
        settings,
        "openclaw_distill_sidecar_factory",
    ):
        conflicts.append(
            "AUTOCONTEXT_OFFLINE is set and an OpenClaw distillation sidecar is configured; "
            "an external sidecar cannot be covered by the no-egress guarantee."
        )
    return conflicts


def _setting(settings: Any, name: str) -> str:
    return str(getattr(settings, name, "") or "").strip().lower()


def _resolve_auto_judge_provider(settings: Any) -> str:
    """Mirror registry.resolve_auto_judge_provider without creating a cycle."""
    inherited = {"claude-cli", "codex", "pi", "pi-rpc"}
    for field_name in (
        "competitor_provider",
        "architect_provider",
        "analyst_provider",
        "coach_provider",
        "agent_provider",
    ):
        provider = _setting(settings, field_name)
        if provider:
            return provider if provider in inherited else "anthropic"
    return "anthropic"


def _provider_conflict(
    setting_name: str,
    provider: str,
    base_url: str,
    settings: Any,
) -> str | None:
    if not provider or provider in {"deterministic", "mlx"}:
        return None
    if provider in UNAVAILABLE_OFFLINE_RUNTIMES:
        return (
            f"AUTOCONTEXT_OFFLINE is set and {setting_name}={provider}, which starts external code "
            "whose network access this process cannot control. Use a local endpoint (ollama, vllm, mlx) instead."
        )
    if provider == "openclaw":
        runtime_kind = _setting(settings, "openclaw_runtime_kind") or "factory"
        if runtime_kind == "http":
            endpoint = _setting(settings, "openclaw_agent_http_endpoint")
            if endpoint and is_local_endpoint(endpoint):
                return None
            return (
                f"AUTOCONTEXT_OFFLINE is set and {setting_name}=openclaw uses a non-local HTTP endpoint "
                f"({endpoint or 'not configured'})."
            )
        return (
            f"AUTOCONTEXT_OFFLINE is set and {setting_name}=openclaw uses the {runtime_kind} runtime; "
            "external code cannot be covered by the no-egress guarantee."
        )
    if provider == "anthropic":
        return f"AUTOCONTEXT_OFFLINE is set and {setting_name}=anthropic; the Anthropic API is remote."
    if provider in _HTTP_PROVIDER_DEFAULTS:
        endpoint = base_url or _HTTP_PROVIDER_DEFAULTS[provider]
        if is_local_endpoint(endpoint):
            return None
        return (
            f"AUTOCONTEXT_OFFLINE is set and {setting_name}={provider} targets a non-local endpoint ({endpoint})."
        )
    return None
