"""Production composition for the bounded campaign-auditor checkpoint runner."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from autocontext.agents.provider_bridge import create_role_client, resolved_role_base_url
from autocontext.audit import (
    CampaignAuditCheckpointRunner,
    CampaignAuditConfig,
    CampaignAuditor,
    CampaignAuditPacketIdentity,
    CampaignAuditRoute,
    CampaignCheckpointPacketFactory,
    build_cancellable_auditor_client,
)
from autocontext.audit.campaign_audit_store import CampaignAuditStore
from autocontext.config.settings import AppSettings


def build_live_campaign_auditor(
    settings: AppSettings,
    *,
    identity: CampaignAuditPacketIdentity,
    store_root: Path,
    scenario_name: str = "",
    proposer_routes: Sequence[CampaignAuditRoute | tuple[str, str]] | None = None,
) -> CampaignAuditCheckpointRunner | None:
    """Build the configured, durable, hard-cancellable auditor.

    The feature is opt-in. Once enabled, construction fails closed when the
    requested independent route cannot be created; silently reusing the
    proposer route would defeat the purpose of an independent audit.
    """

    if not settings.campaign_auditor_enabled:
        return None
    auditor_route = campaign_auditor_route(settings)
    client = create_role_client(
        settings.campaign_auditor_provider,
        settings,
        model_override=settings.campaign_auditor_model,
        scenario_name=scenario_name,
        role="campaign_auditor",
    )
    if client is None:
        raise ValueError("campaign auditor provider must resolve to a concrete model client")
    auditor = CampaignAuditor(
        CampaignAuditConfig(
            enabled=True,
            provider=settings.campaign_auditor_provider,
            model=settings.campaign_auditor_model,
            auditor_route=auditor_route,
            proposer_provider=settings.campaign_auditor_proposer_provider,
            proposer_model=settings.campaign_auditor_proposer_model,
            proposer_routes=[_coerce_route(route) for route in proposer_routes or ()],
            allow_same_route=settings.campaign_auditor_allow_same_route,
            max_calls_per_campaign=settings.campaign_auditor_max_calls_per_campaign,
            max_input_chars=settings.campaign_auditor_max_input_chars,
            max_output_tokens=settings.campaign_auditor_max_output_tokens,
            timeout_seconds=settings.campaign_auditor_timeout_seconds,
            policy=settings.campaign_auditor_policy,
            input_cost_per_million=settings.campaign_auditor_input_cost_per_million,
            output_cost_per_million=settings.campaign_auditor_output_cost_per_million,
        ),
        client=build_cancellable_auditor_client(client),
        store=CampaignAuditStore(store_root),
    )
    return CampaignAuditCheckpointRunner(auditor, CampaignCheckpointPacketFactory(identity))


def campaign_auditor_route(settings: AppSettings) -> CampaignAuditRoute:
    """Resolve and validate the exact independent backend before client construction."""

    provider = settings.campaign_auditor_provider.strip().lower()
    configured_base = settings.campaign_auditor_base_url.strip()
    configured_key = settings.campaign_auditor_api_key.strip()
    endpoint_providers = {"openai", "openai-compatible", "openrouter", "ollama", "vllm", "hermes"}
    credential_providers = {"anthropic", "openai", "openai-compatible", "openrouter", "vllm", "hermes"}
    if configured_base and provider not in endpoint_providers:
        raise ValueError(f"campaign auditor provider {provider!r} does not support a dedicated base URL")
    if configured_key and provider not in credential_providers:
        raise ValueError(f"campaign auditor provider {provider!r} does not support a dedicated API key")
    base_url = resolved_role_base_url(provider, settings, role="campaign_auditor")
    return CampaignAuditRoute.resolved(
        provider,
        settings.campaign_auditor_model,
        base_url=base_url,
    )


def _coerce_route(route: CampaignAuditRoute | tuple[str, str]) -> CampaignAuditRoute:
    if isinstance(route, CampaignAuditRoute):
        return route
    provider, model = route
    return CampaignAuditRoute.resolved(provider, model)


__all__ = ["build_live_campaign_auditor", "campaign_auditor_route"]
