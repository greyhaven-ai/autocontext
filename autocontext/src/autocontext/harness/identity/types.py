"""Agent identity types — traits, soul documents, and composite identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class IdentityTrait:
    """Single measured trait of an agent's behavioural profile."""

    name: str  # "advance_rate", "cost_efficiency", etc.
    value: float
    trend: float  # delta since last evaluation
    observations: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "trend": self.trend,
            "observations": self.observations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdentityTrait:
        return cls(
            name=data["name"],
            value=data["value"],
            trend=data["trend"],
            observations=data["observations"],
        )


@dataclass(frozen=True, slots=True)
class SoulDocument:
    """Immutable declaration of what an agent IS — its purpose, principles, and constraints."""

    role: str
    purpose: str  # what the agent IS
    principles: tuple[str, ...]  # non-negotiable values
    constraints: tuple[str, ...]  # hard limits

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "purpose": self.purpose,
            "principles": list(self.principles),
            "constraints": list(self.constraints),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SoulDocument:
        return cls(
            role=data["role"],
            purpose=data["purpose"],
            principles=tuple(data["principles"]),
            constraints=tuple(data["constraints"]),
        )

    def to_prompt_section(self) -> str:
        lines = [
            f"## Soul: {self.role}",
            "",
            f"**Purpose:** {self.purpose}",
            "",
            "**Principles:**",
        ]
        for p in self.principles:
            lines.append(f"- {p}")
        lines.append("")
        lines.append("**Constraints:**")
        for c in self.constraints:
            lines.append(f"- {c}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Composite identity for a single agent role."""

    role: str
    soul: SoulDocument | None
    traits: tuple[IdentityTrait, ...]
    trust_tier: str
    total_generations: int
    total_advances: int
    created_at: str
    last_updated: str
    history: tuple[dict[str, Any], ...] = ()  # last N snapshots

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def trait(self, name: str) -> IdentityTrait | None:
        """Return the trait with matching *name*, or ``None`` if not found."""
        for t in self.traits:
            if t.name == name:
                return t
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "soul": self.soul.to_dict() if self.soul is not None else None,
            "traits": [t.to_dict() for t in self.traits],
            "trust_tier": self.trust_tier,
            "total_generations": self.total_generations,
            "total_advances": self.total_advances,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentIdentity:
        return cls(
            role=data["role"],
            soul=SoulDocument.from_dict(data["soul"]) if data.get("soul") is not None else None,
            traits=tuple(IdentityTrait.from_dict(t) for t in data["traits"]),
            trust_tier=data["trust_tier"],
            total_generations=data["total_generations"],
            total_advances=data["total_advances"],
            created_at=data["created_at"],
            last_updated=data["last_updated"],
            history=tuple(data.get("history", ())),
        )

    def to_prompt_section(self) -> str:
        lines = [
            f"## Agent Identity: {self.role}",
            "",
            f"**Trust Tier:** {self.trust_tier}",
            f"**Experience:** {self.total_generations} generations, {self.total_advances} advances",
            "",
            "**Traits:**",
            "| Trait | Value | Trend |",
            "|-------|-------|-------|",
        ]
        for t in self.traits:
            lines.append(f"| {t.name} | {t.value:.3f} | {t.trend:+.3f} |")
        if self.soul is not None:
            lines.append("")
            lines.append(self.soul.to_prompt_section())
        return "\n".join(lines)
