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

from autocontext.agents import role_routing_contract_generated as _contract

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


# The values below derive from docs/role-routing-contract.json via the
# generated module autocontext.agents.role_routing_contract_generated.
# To change a value, edit the contract and regenerate — do not edit here.

# The contract is the source of truth for which provider classes exist;
# ProviderClass is the hand-written enum type. If the contract declares a
# class the enum doesn't have, fail loudly here instead of deep inside a
# ProviderClass(name) call during a routing decision.
_MISSING_CLASSES = set(_contract.PROVIDER_CLASSES) - {member.value for member in ProviderClass}
if _MISSING_CLASSES:
    raise RuntimeError(
        f"role-routing contract declares provider classes with no ProviderClass member: "
        f"{sorted(_MISSING_CLASSES)}. Add them to the enum."
    )

# Approximate cost per 1K input tokens by provider class
_COST_TABLE: dict[ProviderClass, float] = {ProviderClass(name): cost for name, cost in _contract.COST_PER_1K_TOKENS.items()}

# Default routing table: role → ordered list of preferred provider classes
# First match that's available wins; last entry is the fallback.
DEFAULT_ROUTING_TABLE: dict[str, list[ProviderClass]] = {
    role: [ProviderClass(name) for name in preferences] for role, preferences in _contract.DEFAULT_ROUTING_TABLE.items()
}

# Capability ordering, so a role's requirement can be compared against what an
# endpoint declares. Only the API-backed classes are ranked: LOCAL names an
# artifact slot rather than a capability, and CODE_POLICY is not model-backed.
_CAPABILITY_RANK: dict[ProviderClass, int] = {ProviderClass(name): rank for name, rank in _contract.CAPABILITY_RANK.items()}

# Where a transport runs. Orthogonal to capability: a self-hosted endpoint can be
# frontier-class and a cloud endpoint can be fast. Unknown transports are treated
# as remote, so an unrecognized name never silently reports zero cost.
_PROVIDER_HOSTING: dict[str, str] = dict(_contract.PROVIDER_HOSTING)

# What a distilled local artifact is treated as being capable of.
_LOCAL_ARTIFACT_CAPABILITY = ProviderClass(_contract.LOCAL_ARTIFACT_CAPABILITY)


def _role_minimum_capability(preferences: list[ProviderClass]) -> ProviderClass | None:
    """The capability a role needs: its first API-backed preference."""
    for pref in preferences:
        if pref in _CAPABILITY_RANK:
            return pref
    return None


# Roles a local artifact may serve. Derived from the routing table and the
# artifact's declared capability rather than enumerated (AC-911): a role is
# eligible when it lists LOCAL as a preference and the artifact is at least as
# capable as the role requires. Declaring the artifact less capable therefore
# narrows this automatically, instead of requiring a second list to be edited in
# step with the first.
_LOCAL_ELIGIBLE_ROLES: set[str] = {
    role
    for role, preferences in DEFAULT_ROUTING_TABLE.items()
    if ProviderClass.LOCAL in preferences
    and (minimum := _role_minimum_capability(preferences)) is not None
    and _CAPABILITY_RANK[_LOCAL_ARTIFACT_CAPABILITY] >= _CAPABILITY_RANK[minimum]
}

# Capability inferred from the transport name, used only when the endpoint has
# not declared one. Retained rather than deleted because cloud transports do have
# a knowable capability; it is self-hosted endpoints that inference gets wrong.
_EXPLICIT_PROVIDER_CLASS: dict[str, ProviderClass] = {
    provider: ProviderClass(name) for provider, name in _contract.EXPLICIT_PROVIDER_CLASSES.items()
}


def _is_locally_hosted(provider_type: str) -> bool:
    """Whether this transport runs on the user's own hardware."""
    return _PROVIDER_HOSTING.get(provider_type.strip().lower()) == "local"


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
        }
        # AC-912: the settings field backing each slot, so model resolution can
        # ask whether the user actually configured it. Kept beside the tables
        # above rather than derived, because a wrong mapping here would
        # silently resolve the wrong field.
        self._class_model_fields: dict[ProviderClass, str] = {
            ProviderClass.FRONTIER: "tier_opus_model",
            ProviderClass.MID_TIER: "tier_sonnet_model",
            ProviderClass.FAST: "tier_haiku_model",
        }
        self._role_model_fields: dict[str, str] = {
            "competitor": "model_competitor",
            "analyst": "model_analyst",
            "coach": "model_coach",
            "architect": "model_architect",
            "translator": "model_translator",
            "curator": "model_curator",
        }
        self._role_providers: dict[str, str] = {
            "competitor": settings.competitor_provider,
            "analyst": settings.analyst_provider,
            "coach": settings.coach_provider,
            "architect": settings.architect_provider,
        }
        self._declared_capability = self._parse_declared_capability(settings.provider_capability)

    @staticmethod
    def _parse_declared_capability(raw: str) -> ProviderClass | None:
        """Validate the declared capability once, at construction.

        Raising here rather than at route() time means a typo surfaces when the
        run is being set up, not partway through a generation when one role
        happens to take the path that reads it.
        """
        declared = (raw or "").strip().lower()
        if not declared:
            return None
        try:
            capability = ProviderClass(declared)
        except ValueError:
            capability = None
        if capability is None or capability not in _CAPABILITY_RANK:
            raise ValueError(
                f"AUTOCONTEXT_PROVIDER_CAPABILITY={raw!r} is not a capability class. "
                f"Expected one of: {', '.join(sorted(c.value for c in _CAPABILITY_RANK))}."
            )
        return capability

    def _effective_capability(self, provider_type: str, inferred: ProviderClass) -> ProviderClass:
        """The endpoint's capability, preferring what the user declared.

        A declaration only applies to locally hosted transports. Cloud transports
        have a knowable capability, and confining the override to self-hosted
        endpoints is what makes "Anthropic routing is unchanged" hold without
        depending on the user not setting this.

        ProviderClass.LOCAL is left alone: it names the distilled artifact slot,
        and the orchestrator reads it as a signal that the model is
        mlx_model_path.
        """
        if self._declared_capability is None or inferred == ProviderClass.LOCAL:
            return inferred
        if not _is_locally_hosted(provider_type):
            return inferred
        return self._declared_capability

    def _cost_for(self, provider_class: ProviderClass, provider_type: str) -> float:
        """Cost is a function of hosting, not capability (AC-911).

        Self-hosted inference has no per-token API cost, however capable the
        model behind it is. Keying this on capability is what made a fully
        self-hosted run report the same $/1k as an all-Anthropic one.
        """
        if _is_locally_hosted(provider_type):
            return 0.0
        return _COST_TABLE.get(provider_class, 0.003)

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
        explicit = self._role_providers.get(role, "")
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
        preferences = self._table.get(role, [ProviderClass.MID_TIER])

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

    def _resolve_role_model(self, role: str, provider: str) -> str | None:
        """Role model for ``provider``, honoring an explicit override first."""
        from autocontext.config.provider_model_defaults import resolve_model_default

        field = self._role_model_fields.get(role)
        configured = self._role_models.get(role)
        if field is None:
            # An unregistered role has no backing field to check, so there is
            # nothing to resolve against -- preserve today's behavior exactly.
            return configured
        return resolve_model_default(
            self._settings,
            provider=provider,
            field_name=field,
            configured=configured,
        )

    def role_model_is_explicit(self, role: str) -> bool:
        """Whether the caller explicitly configured this role's model slot."""
        field = self._role_model_fields.get(role)
        return field is not None and field in self._settings.model_fields_set

    def resolved_role_model(self, role: str, provider: str) -> str | None:
        """Resolve a role slot for the effective provider."""
        return self._resolve_role_model(role, provider)

    def _resolve_class_model(self, role: str, provider_class: ProviderClass, provider: str) -> str | None:
        """Tier model for ``provider``, falling back to the role model."""
        from autocontext.config.provider_model_defaults import resolve_model_default

        field = self._class_model_fields.get(provider_class)
        configured = self._class_to_model.get(provider_class)
        if field is None or configured is None:
            return self._resolve_role_model(role, provider)
        return resolve_model_default(
            self._settings,
            provider=provider,
            field_name=field,
            configured=configured,
        )

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
                estimated_cost_per_1k_tokens=self._cost_for(ProviderClass.LOCAL, "mlx"),
            )
        provider_type = self._settings.agent_provider
        # A role asks for a capability; an endpoint has one. Asking for frontier
        # from an endpoint declared mid_tier does not make it frontier, so the
        # request is clamped down to what the endpoint actually offers and both
        # the tier model and the reported class follow the clamped value.
        effective = self._clamp_to_declared(provider_type, provider_class)
        return ProviderConfig(
            provider_type=provider_type,
            model=self._resolve_class_model(role, effective, provider_type),
            provider_class=effective,
            estimated_cost_per_1k_tokens=self._cost_for(effective, provider_type),
        )

    def _clamp_to_declared(self, provider_type: str, requested: ProviderClass) -> ProviderClass:
        """Lower a requested capability to what the endpoint declares, never raise it."""
        if requested not in _CAPABILITY_RANK:
            return requested
        declared = self._effective_capability(provider_type, requested)
        if declared not in _CAPABILITY_RANK:
            return requested
        return declared if _CAPABILITY_RANK[declared] < _CAPABILITY_RANK[requested] else requested

    def _config_for_explicit(self, role: str, provider_type: str) -> ProviderConfig:
        """Build config when an explicit per-role provider is set."""
        inferred = _EXPLICIT_PROVIDER_CLASS.get(
            provider_type.lower(),
            ProviderClass.FRONTIER,
        )
        provider_class = self._effective_capability(provider_type, inferred)
        return ProviderConfig(
            provider_type=provider_type,
            model=(
                self._settings.mlx_model_path
                if provider_class == ProviderClass.LOCAL
                else self._resolve_role_model(role, provider_type)
            ),
            provider_class=provider_class,
            estimated_cost_per_1k_tokens=self._cost_for(provider_class, provider_type),
        )

    def _config_for_default(self, role: str) -> ProviderConfig:
        """Build config when routing is disabled — use default provider + model."""
        provider_type = self._settings.agent_provider
        inferred = _EXPLICIT_PROVIDER_CLASS.get(
            provider_type.lower(),
            ProviderClass.MID_TIER,
        )
        provider_class = self._effective_capability(provider_type, inferred)
        return ProviderConfig(
            provider_type=provider_type,
            model=(
                self._settings.mlx_model_path
                if provider_class == ProviderClass.LOCAL
                else self._resolve_role_model(role, provider_type)
            ),
            provider_class=provider_class,
            estimated_cost_per_1k_tokens=self._cost_for(provider_class, provider_type),
        )
