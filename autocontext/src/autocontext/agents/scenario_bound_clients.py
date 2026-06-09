"""Scenario-bound agent client resolution, extracted from orchestrator.py for module size.

These resolve a per-scenario agent client at role-execution time, once the scenario is known
(the orchestrator itself is built before the scenario): the MLX recursive-loop model from the
registry, and pi / pi-rpc runtime handoffs. Functions take the orchestrator as ``orch`` so
they reuse its routed-client cache and client wrapping.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autocontext.agents.llm_client import LanguageModelClient, build_client_from_settings
from autocontext.agents.role_runtime_overrides import settings_for_budgeted_role_call

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator

logger = logging.getLogger(__name__)


def scenario_bound_mlx_client(orch: AgentOrchestrator, role: str, *, scenario_name: str) -> LanguageModelClient | None:
    """Build the MLX agent client resolved from the registry for this scenario (recursive loop).

    Returns ``None`` when no active model is resolvable, so the caller falls back to the
    default client. ``build_client_from_settings`` does the registry-aware path resolution.
    """
    key = ("mlx", None, scenario_name, role)
    cached = orch._routed_clients.get(key)
    if cached is not None:
        return cached
    try:
        client = build_client_from_settings(orch.settings, scenario_name=scenario_name)
    except Exception:
        logger.debug("agents.scenario_bound_clients: no active mlx model for scenario", exc_info=True)
        return None
    client = orch._wrap_client(client, provider_name=f"mlx:{role}")
    orch._routed_clients[key] = client
    return client


def scenario_bound_runtime_client(
    orch: AgentOrchestrator, provider_type: str, role: str, *, scenario_name: str
) -> LanguageModelClient | None:
    """Resolve a scenario-bound client for the given provider, or ``None`` to fall back.

    ``mlx`` resolves the harness-trained model from the registry; ``pi`` / ``pi-rpc`` resolve
    a runtime handoff. Other providers have no scenario-bound form.
    """
    if not scenario_name:
        return None
    if provider_type == "mlx":
        return scenario_bound_mlx_client(orch, role, scenario_name=scenario_name)
    if provider_type not in {"pi", "pi-rpc"}:
        return None

    from autocontext.agents.provider_bridge import create_role_client

    call_settings, is_budgeted = settings_for_budgeted_role_call(
        orch.settings, provider_type, role, orch._active_generation_deadline
    )
    key = (provider_type.lower(), None, scenario_name, role)
    if not is_budgeted:
        cached = orch._routed_clients.get(key)
        if cached is not None:
            return cached

    client = create_role_client(provider_type, call_settings, scenario_name=scenario_name, role=role)
    if client is not None:
        client = orch._wrap_client(client, provider_name=f"{provider_type}:{role}")
        if is_budgeted:
            orch._mark_disposable_client(client)
        else:
            orch._routed_clients[key] = client
    return client
