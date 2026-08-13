from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from autocontext.agents.subagent_runtime import SubagentRuntime
from autocontext.config.settings import AppSettings

if TYPE_CHECKING:
    from autocontext.agents.role_router import ProviderConfig, RoleRouter

logger = logging.getLogger(__name__)

_ROLE_RUNTIME_TIMEOUT_FIELDS = {
    "pi": "pi_timeout",
    "pi-rpc": "pi_timeout",
    "claude-cli": "claude_timeout",
    "codex": "codex_timeout",
    "hermes": "hermes_timeout",
}


def resolve_routed_provider_config(
    router: RoleRouter,
    role: str,
    config: ProviderConfig,
    dynamic_model: str | None,
) -> tuple[ProviderConfig, str | None]:
    """Apply final model precedence before constructing a routed client.

    OpenAI-shaped bridges use the provider's configured default and ignore the
    runner's per-call model argument, so the client and returned model must be
    resolved together at this boundary.
    """
    if config.provider_class.value == "local":
        model = config.model
    elif router.role_model_is_explicit(role):
        model = router.resolved_role_model(role, config.provider_type)
    else:
        model = dynamic_model or config.model
    return replace(config, model=model), model


def settings_for_budgeted_role_call(
    settings: AppSettings,
    provider_type: str,
    role: str,
    generation_deadline: float | None,
) -> tuple[AppSettings, bool]:
    field = _ROLE_RUNTIME_TIMEOUT_FIELDS.get(provider_type.lower().strip())
    if field is None or generation_deadline is None:
        return settings, False
    remaining = generation_deadline - time.monotonic()
    if remaining < 1.0:
        raise TimeoutError(
            f"generation time budget exhausted before {role} provider call "
            f"({remaining:.2f}s remaining)"
        )
    configured = float(getattr(settings, field))
    bounded = min(configured, remaining)
    if bounded == configured:
        return settings, False
    updates: dict[str, Any] = {field: bounded}
    if provider_type.lower().strip() == "pi-rpc":
        updates["pi_rpc_persistent"] = False
    return settings.model_copy(update=updates), True


def apply_role_overrides(orch: Any, settings: AppSettings) -> None:
    """Apply per-role provider and credential overrides to an orchestrator."""
    from autocontext.agents.provider_bridge import configured_role_provider, create_role_client, has_role_client_override

    runner_map = {
        "competitor": "competitor",
        "analyst": "analyst",
        "coach": "coach",
        "architect": "architect",
    }

    for role, runner_name in runner_map.items():
        if not has_role_client_override(role, settings):
            continue
        provider_type = configured_role_provider(role, settings) or settings.agent_provider
        client = create_role_client(provider_type, settings, role=role)
        if client is None:
            continue
        client = orch._wrap_client(client, provider_name=f"{provider_type}:{role}")
        orch._role_clients[role] = client
        runtime = SubagentRuntime(
            client=client,
            constrained_output=settings.constrained_output,
        )
        runner = getattr(orch, runner_name)
        runner.runtime = runtime
        logger.info("role '%s' using dedicated provider config: %s", role, provider_type)
