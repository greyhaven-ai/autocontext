"""Capability- and cost-aware role routing (AC-204).

Routes agent roles to executable providers based on capability requirements,
execution cost, and available local artifacts (distilled models).

Usage:
    AUTOCONTEXT_ROLE_ROUTING=auto  — automatic provider selection per role
    AUTOCONTEXT_ROLE_ROUTING=off   — use default provider for all roles (default)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings


class ProviderClass(StrEnum):
    """Classification of provider capabilities."""

    FRONTIER = "frontier"
    MID_TIER = "mid_tier"
    FAST = "fast"
    LOCAL = "local"
    CODE_POLICY = "code_policy"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Result of routing: tells the system what provider/model to use."""

    provider_type: str
    model: str | None
    provider_class: ProviderClass
    estimated_cost_per_1k_tokens: float


@dataclass(slots=True)
class RoutingContext:
    """Contextual signals for routing decisions."""

    generation: int = 0
    retry_count: int = 0
    is_plateau: bool = False
    available_local_models: list[str] = field(default_factory=list)
    scenario_name: str = ""


# Approximate cost per 1K input tokens by provider class
_COST_TABLE: dict[ProviderClass, float] = {
    ProviderClass.FRONTIER: 0.015,
    ProviderClass.MID_TIER: 0.003,
    ProviderClass.FAST: 0.001,
    ProviderClass.LOCAL: 0.0,
}

# Default routing table: role → ordered list of preferred provider classes
# First match that's available wins; last entry is the fallback.
DEFAULT_ROUTING_TABLE: dict[str, list[ProviderClass]] = {
    "competitor": [ProviderClass.FRONTIER, ProviderClass.LOCAL],
    "analyst": [ProviderClass.MID_TIER, ProviderClass.LOCAL],
    "coach": [ProviderClass.MID_TIER, ProviderClass.LOCAL],
    "architect": [ProviderClass.FRONTIER],
    "curator": [ProviderClass.FAST],
    "translator": [ProviderClass.FAST, ProviderClass.LOCAL],
    "librarian": [ProviderClass.MID_TIER, ProviderClass.LOCAL],
    "archivist": [ProviderClass.FRONTIER],
}

# Roles that can be served by local artifacts when available
_LOCAL_ELIGIBLE_ROLES: set[str] = {"competitor", "analyst", "coach", "translator"}

# Provider type inferred from provider class when using the default provider
_EXPLICIT_PROVIDER_CLASS: dict[str, ProviderClass] = {
    "anthropic": ProviderClass.FRONTIER,
    "mlx": ProviderClass.LOCAL,
    "openclaw": ProviderClass.FRONTIER,
    "deterministic": ProviderClass.FAST,
    "agent_sdk": ProviderClass.FRONTIER,
    "openai": ProviderClass.MID_TIER,
    "openai-compatible": ProviderClass.MID_TIER,
    "ollama": ProviderClass.MID_TIER,
    "vllm": ProviderClass.MID_TIER,
}


class RoleRouter:
    """Routes agent roles to providers based on capability, cost, and available artifacts."""

    def __init__(
        self,
        settings: AppSettings,
        routing_table: dict[str, list[ProviderClass]] | None = None,
    ) -> None:
        self._settings = settings
        self._table = routing_table if routing_table is not None else dict(DEFAULT_ROUTING_TABLE)
        self._class_to_model: dict[ProviderClass, str] = {
            ProviderClass.FRONTIER: settings.tier_opus_model,
            ProviderClass.MID_TIER: settings.tier_sonnet_model,
            ProviderClass.FAST: settings.tier_haiku_model,
            ProviderClass.LOCAL: settings.mlx_model_path,
        }
        self._role_models: dict[str, str] = {
            "competitor": settings.model_competitor,
            "analyst": settings.model_analyst,
            "coach": settings.model_coach,
            "architect": settings.model_architect,
            "translator": settings.model_translator,
            "curator": settings.model_curator,
            "librarian": settings.model_librarian,
            "archivist": settings.model_archivist,
        }
        self._role_providers: dict[str, str] = {
            "competitor": settings.competitor_provider,
            "analyst": settings.analyst_provider,
            "coach": settings.coach_provider,
            "architect": settings.architect_provider,
            "librarian": settings.librarian_provider,
            "archivist": settings.archivist_provider,
        }

    def _resolve_role_key(self, role: str, table: dict[str, str]) -> str:
        """Lookup with prefix fallback for librarian_* roles."""
        if role in table:
            return table[role]
        if role.startswith("librarian_"):
            return table.get("librarian", "")
        return table.get(role, "")

    def _resolve_routing_table(self, role: str) -> list[ProviderClass]:
        """Routing table lookup with prefix fallback for librarian_* roles."""
        if role in self._table:
            return self._table[role]
        if role.startswith("librarian_"):
            return self._table.get("librarian", [ProviderClass.MID_TIER])
        return [ProviderClass.MID_TIER]

    def resolve_model(self, role: str) -> str:
        """Resolve model for a role, with prefix fallback."""
        return self._resolve_role_key(role, self._role_models) or self._settings.model_competitor

    def resolve_provider(self, role: str) -> str:
        """Resolve provider override for a role, with prefix fallback."""
        return self._resolve_role_key(role, self._role_providers)

    def route(
        self,
        role: str,
        context: RoutingContext | None = None,
    ) -> ProviderConfig:
        """Select the best provider config for a role.

        Priority:
        1. Explicit per-role provider override (AUTOCONTEXT_{ROLE}_PROVIDER)
        2. Auto routing from routing table + available artifacts
        3. Default provider with configured model
        """
        ctx = context or RoutingContext()

        # 1. Check explicit per-role override
        explicit = self._resolve_role_key(role, self._role_providers)
        if explicit:
            return self._config_for_explicit(role, explicit)

        # 2. If routing is disabled, return default
        if self._settings.role_routing != "auto":
            return self._config_for_default(role)

        # 3. Auto routing
        return self._auto_route(role, ctx)

    def estimate_run_cost(
        self,
        context: RoutingContext | None = None,
    ) -> dict[str, Any]:
        """Estimate per-role and total cost for one generation cycle.

        Returns dict with per-role breakdown and savings vs all-frontier.
        """
        roles = ["competitor", "analyst", "coach", "architect", "curator", "translator"]
        role_costs: dict[str, dict[str, Any]] = {}
        total = 0.0
        all_frontier = 0.0

        for role in roles:
            cfg = self.route(role, context=context)
            cost = cfg.estimated_cost_per_1k_tokens
            total += cost
            all_frontier += _COST_TABLE[ProviderClass.FRONTIER]
            role_costs[role] = {
                "provider_class": cfg.provider_class,
                "provider_type": cfg.provider_type,
                "cost_per_1k_tokens": cost,
            }

        return {
            "total_per_1k_tokens": total,
            "all_frontier_per_1k_tokens": all_frontier,
            "savings_vs_all_frontier": all_frontier - total,
            "roles": role_costs,
        }

    def _auto_route(self, role: str, ctx: RoutingContext) -> ProviderConfig:
        """Select provider class from routing table, considering available artifacts.

        Local artifacts and code policies are preferred when available and the
        role is eligible, since they reduce cost to zero. Otherwise the first
        API-backed preference in the table is used.
        """
        preferences = self._resolve_routing_table(role)

        # First pass: check if any artifact-backed preference is satisfied
        for pref in preferences:
            if pref == ProviderClass.LOCAL and role in _LOCAL_ELIGIBLE_ROLES and ctx.available_local_models:
                return self._config_for_class(role, ProviderClass.LOCAL, local_model_path=ctx.available_local_models[0])

        # Second pass: use the first API-backed preference
        for pref in preferences:
            if pref in (ProviderClass.FRONTIER, ProviderClass.MID_TIER, ProviderClass.FAST):
                return self._config_for_class(role, pref)

        # Fallback
        return self._config_for_class(role, preferences[0] if preferences else ProviderClass.MID_TIER)

    def _config_for_class(
        self,
        role: str,
        provider_class: ProviderClass,
        *,
        local_model_path: str | None = None,
    ) -> ProviderConfig:
        """Build a ProviderConfig for a resolved provider class."""
        if provider_class == ProviderClass.LOCAL:
            return ProviderConfig(
                provider_type="mlx",
                model=local_model_path or self._settings.mlx_model_path or None,
                provider_class=ProviderClass.LOCAL,
                estimated_cost_per_1k_tokens=_COST_TABLE[ProviderClass.LOCAL],
            )
        return ProviderConfig(
            provider_type=self._settings.agent_provider,
            model=self._class_to_model.get(provider_class, self._resolve_role_key(role, self._role_models)),
            provider_class=provider_class,
            estimated_cost_per_1k_tokens=_COST_TABLE.get(provider_class, 0.003),
        )

    def _config_for_explicit(self, role: str, provider_type: str) -> ProviderConfig:
        """Build config when an explicit per-role provider is set."""
        provider_class = _EXPLICIT_PROVIDER_CLASS.get(
            provider_type.lower(), ProviderClass.FRONTIER,
        )
        return ProviderConfig(
            provider_type=provider_type,
            model=self._settings.mlx_model_path if provider_class == ProviderClass.LOCAL else self._resolve_role_key(role, self._role_models),
            provider_class=provider_class,
            estimated_cost_per_1k_tokens=_COST_TABLE.get(provider_class, 0.003),
        )

    def _config_for_default(self, role: str) -> ProviderConfig:
        """Build config when routing is disabled — use default provider + model."""
        provider_class = _EXPLICIT_PROVIDER_CLASS.get(
            self._settings.agent_provider.lower(), ProviderClass.MID_TIER,
        )
        return ProviderConfig(
            provider_type=self._settings.agent_provider,
            model=self._settings.mlx_model_path if provider_class == ProviderClass.LOCAL else self._resolve_role_key(role, self._role_models),
            provider_class=provider_class,
            estimated_cost_per_1k_tokens=_COST_TABLE.get(provider_class, 0.003),
        )
