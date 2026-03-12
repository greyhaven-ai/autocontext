"""Identity evolution — derives agent traits from performance profiles."""

from __future__ import annotations

from autocontext.harness.audit.types import AuditCategory, AuditEntry
from autocontext.harness.audit.writer import AppendOnlyAuditWriter
from autocontext.harness.identity.types import AgentIdentity, IdentityTrait
from autocontext.harness.meta.types import RoleProfile
from autocontext.harness.trust.types import TrustScore

MAX_HISTORY = 10

# Finite cap for cost_per_advance when it is infinity (keeps values JSON-serializable).
_INF_CAP = 999999.0

# Trait names derived from a RoleProfile.
_TRAIT_NAMES = (
    "advance_rate",
    "mean_cost_per_gen",
    "cost_per_advance",
    "token_efficiency",
    "mean_latency_ms",
)


class IdentityEvolver:
    """Evolves agent identities based on measured performance."""

    def __init__(self, audit_writer: AppendOnlyAuditWriter | None = None) -> None:
        self._audit_writer = audit_writer

    def evolve(
        self,
        identity: AgentIdentity,
        profile: RoleProfile,
        trust_score: TrustScore | None = None,
    ) -> AgentIdentity:
        """Return a new identity with traits derived from *profile*.

        The input *identity* is never mutated.
        """
        # Build a lookup of previous trait values for trend computation.
        prev_traits: dict[str, float] = {t.name: t.value for t in identity.traits}

        observations = profile.generations_observed

        # Extract raw values from profile, capping infinity.
        raw_values: dict[str, float] = {
            "advance_rate": profile.advance_rate,
            "mean_cost_per_gen": profile.mean_cost_per_gen,
            "cost_per_advance": min(profile.cost_per_advance, _INF_CAP),
            "token_efficiency": profile.token_efficiency,
            "mean_latency_ms": profile.mean_latency_ms,
        }

        new_traits: list[IdentityTrait] = []
        for name in _TRAIT_NAMES:
            value = raw_values[name]
            trend = value - prev_traits.get(name, value)
            new_traits.append(
                IdentityTrait(
                    name=name,
                    value=value,
                    trend=trend,
                    observations=observations,
                )
            )

        # Determine trust tier.
        old_tier = identity.trust_tier
        new_tier = trust_score.tier.value if trust_score is not None else old_tier

        # Append previous state to history, cap at MAX_HISTORY (keep most recent).
        history = list(identity.history)
        history.append(identity.to_dict())
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]

        new_identity = AgentIdentity(
            role=identity.role,
            soul=identity.soul,
            traits=tuple(new_traits),
            trust_tier=new_tier,
            total_generations=profile.generations_observed,
            total_advances=round(profile.advance_rate * profile.generations_observed),
            created_at=identity.created_at,
            last_updated=AgentIdentity.now(),
            history=tuple(history),
        )

        # Audit tier changes.
        if self._audit_writer is not None and new_tier != old_tier:
            self._audit_writer.append(
                AuditEntry(
                    timestamp=AuditEntry.now(),
                    category=AuditCategory.CONFIG_CHANGE,
                    actor="identity_evolver",
                    action=f"tier_change:{identity.role}",
                    detail=f"{old_tier} -> {new_tier}",
                )
            )

        return new_identity
