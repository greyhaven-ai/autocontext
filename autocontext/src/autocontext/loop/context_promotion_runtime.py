"""Wire configured context promotion into live generation runs."""

from __future__ import annotations

from autocontext.agents import role_runtime_overrides
from autocontext.agents.model_router import ModelRouter, TierConfig
from autocontext.agents.provider_bridge import resolved_role_base_url
from autocontext.agents.role_router import RoleRouter, RoutingContext, available_local_models
from autocontext.audit import CampaignAuditPacketIdentity, CampaignAuditRoute
from autocontext.campaign_audit_runtime import build_live_campaign_auditor
from autocontext.config.provider_model_defaults import resolve_model_default
from autocontext.config.settings import AppSettings
from autocontext.context_bundles import evaluator_epoch_for
from autocontext.context_bundles.assembly import LIVE_CONTEXT_ROUTING_FIELDS, bundle_routing_config
from autocontext.context_bundles.promotion import ContextBundlePromotionCoordinator
from autocontext.execution.supervisor import ExecutionSupervisor
from autocontext.extensions import HookBus
from autocontext.loop.live_context_promotion import (
    LiveContextPromotionConfig,
    build_live_context_promotion,
)
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.scenario_paths import resolve_scenario_root


def build_context_promotion_for_run(
    settings: AppSettings,
    *,
    scenario_name: str,
    scenario: object,
    run_id: str,
    generation_index: int,
    artifacts: ArtifactStore,
    orchestrator: object,
    supervisor: ExecutionSupervisor,
    hook_bus: HookBus,
    explicit: ContextBundlePromotionCoordinator | None = None,
) -> ContextBundlePromotionCoordinator | None:
    """Return an injected coordinator or compose the configured live one."""

    if settings.campaign_auditor_enabled and not settings.context_bundle_promotion_enabled:
        raise ValueError(
            "campaign_auditor_enabled requires context_bundle_promotion_enabled for generation runs; "
            "campaign-plan runs provide their own audit checkpoints"
        )
    if explicit is not None:
        return explicit
    if not settings.context_bundle_promotion_enabled:
        return None
    evaluator_epoch = evaluator_epoch_for(scenario, settings)
    context_root = resolve_scenario_root(settings.knowledge_root, scenario_name) / "context_bundles"
    proposer_routes = _proposer_routes(
        settings,
        artifacts=artifacts,
        scenario_name=scenario_name,
        generation_index=generation_index,
    )
    lifecycle_auditor = build_live_campaign_auditor(
        settings,
        identity=CampaignAuditPacketIdentity(
            campaign_id=run_id,
            run_id=run_id,
            scenario_name=scenario_name,
            artifact_uri=str(context_root),
            evaluator_epoch=evaluator_epoch,
            verifier_contract_ref=f"runtime-context-bundle-v1:{evaluator_epoch}",
        ),
        store_root=settings.runs_root / "campaign-audits",
        scenario_name=scenario_name,
        proposer_routes=proposer_routes,
    )
    return build_live_context_promotion(
        scenario_name=scenario_name,
        scenario=scenario,
        run_id=run_id,
        cohort_id=f"generation:{generation_index}",
        evaluator_epoch=evaluator_epoch,
        store=artifacts.context_bundle_store,
        orchestrator=orchestrator,
        supervisor=supervisor,
        risk_root=context_root / "promotion-risk",
        config=LiveContextPromotionConfig(
            min_screen_pairs=settings.context_bundle_promotion_min_screen_pairs,
            min_confirmation_pairs=settings.context_bundle_promotion_min_confirmation_pairs,
            max_confirmation_pairs=settings.context_bundle_promotion_max_confirmation_pairs,
            min_heldout_pairs=settings.context_bundle_promotion_min_heldout_pairs,
            min_effect=settings.context_bundle_promotion_min_effect,
            confidence_z=settings.context_bundle_promotion_confidence_z,
            seed_base=settings.context_bundle_promotion_seed_base,
            timeout_seconds=settings.context_bundle_promotion_eval_timeout_seconds,
            max_memory_mb=settings.context_bundle_promotion_eval_max_memory_mb,
            familywise_alpha=settings.context_bundle_promotion_familywise_alpha,
            allocation_decay=settings.context_bundle_promotion_allocation_decay,
            min_independent_confirmation_blocks=settings.context_bundle_promotion_min_independent_blocks,
            robust_method=settings.context_bundle_promotion_robust_method,
        ),
        hook_bus=hook_bus,
        lifecycle_auditor=lifecycle_auditor,
        generation_index=generation_index,
    )


def _proposer_routes(
    settings: AppSettings,
    *,
    artifacts: ArtifactStore,
    scenario_name: str,
    generation_index: int,
) -> tuple[CampaignAuditRoute, ...]:
    """Resolve every live coach/architect route that can author the candidate."""

    route_settings = [settings]
    active = artifacts.context_bundle_store.active_bundle(scenario_name)
    if active is not None:
        routing = bundle_routing_config(active)
        updates = {field: routing[field] for field in LIVE_CONTEXT_ROUTING_FIELDS if field in routing}
        if updates:
            route_settings.append(settings.model_copy(update=updates))

    resolved: dict[tuple[str, str], CampaignAuditRoute] = {}
    for effective_settings in route_settings:
        local_models = available_local_models(
            effective_settings,
            scenario_name=scenario_name,
            runtime_type="provider",
        )
        context = RoutingContext(
            generation=generation_index,
            available_local_models=local_models,
            scenario_name=scenario_name,
        )
        router = RoleRouter(effective_settings)
        model_router = ModelRouter(
            TierConfig(
                enabled=effective_settings.tier_routing_enabled,
                tier_haiku_model=effective_settings.tier_haiku_model,
                tier_sonnet_model=effective_settings.tier_sonnet_model,
                tier_opus_model=effective_settings.tier_opus_model,
                competitor_haiku_max_gen=effective_settings.tier_competitor_haiku_max_gen,
                harness_aware_tiering_enabled=effective_settings.tier_harness_aware_enabled,
                harness_coverage_demotion_threshold=effective_settings.tier_harness_coverage_demotion_threshold,
            )
        )
        for role in ("coach", "architect"):
            route = router.route(role, context=context)
            tier = model_router.select_tier(
                role,
                generation=generation_index,
                retry_count=0,
                is_plateau=False,
                max_capability=route.provider_class.value,
            )
            dynamic_model: str | None = None
            if tier is not None:
                field_name = f"tier_{tier}_model"
                configured = str(getattr(effective_settings, field_name))
                dynamic_model = (
                    resolve_model_default(
                        effective_settings,
                        provider=route.provider_type,
                        field_name=field_name,
                        configured=configured,
                        provider_class={"haiku": "fast", "sonnet": "mid_tier", "opus": "frontier"}[tier],
                    )
                    or configured
                )
            effective_route, effective_model = role_runtime_overrides.resolve_routed_provider_config(
                router,
                role,
                route,
                dynamic_model,
            )
            provider = effective_route.provider_type.strip()
            model = (effective_model or effective_route.model or "").strip()
            if not provider or not model:
                raise ValueError(f"context candidate proposer route for {role} is incomplete")
            identity = CampaignAuditRoute.resolved(
                provider,
                model,
                base_url=resolved_role_base_url(provider, effective_settings, role=role),
            )
            resolved[identity.independence_identity] = identity
    return tuple(resolved[key] for key in sorted(resolved))


__all__ = ["build_context_promotion_for_run"]
