"""Tests for mts.harness.identity.types — IdentityTrait, SoulDocument, AgentIdentity."""

from __future__ import annotations

import dataclasses
from datetime import datetime

from mts.harness.identity.types import AgentIdentity, IdentityTrait, SoulDocument

# ── IdentityTrait ────────────────────────────────────────────────────────────


def test_identity_trait_construction() -> None:
    trait = IdentityTrait(name="advance_rate", value=0.75, trend=0.05, observations=10)
    assert trait.name == "advance_rate"
    assert trait.value == 0.75
    assert trait.trend == 0.05
    assert trait.observations == 10


def test_identity_trait_frozen() -> None:
    trait = IdentityTrait(name="cost_efficiency", value=0.9, trend=-0.01, observations=5)
    assert dataclasses.is_dataclass(trait)
    try:
        trait.name = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_identity_trait_to_dict() -> None:
    trait = IdentityTrait(name="advance_rate", value=0.75, trend=0.05, observations=10)
    d = trait.to_dict()
    assert d == {
        "name": "advance_rate",
        "value": 0.75,
        "trend": 0.05,
        "observations": 10,
    }


def test_identity_trait_from_dict() -> None:
    data = {"name": "cost_efficiency", "value": 0.9, "trend": -0.01, "observations": 5}
    trait = IdentityTrait.from_dict(data)
    assert trait.name == "cost_efficiency"
    assert trait.value == 0.9
    assert trait.trend == -0.01
    assert trait.observations == 5


# ── SoulDocument ─────────────────────────────────────────────────────────────


def test_soul_document_construction() -> None:
    soul = SoulDocument(
        role="competitor",
        purpose="Generate winning strategies",
        principles=("Always improve", "Never regress"),
        constraints=("Max 1000 tokens", "JSON output only"),
    )
    assert soul.role == "competitor"
    assert soul.purpose == "Generate winning strategies"
    assert soul.principles == ("Always improve", "Never regress")
    assert soul.constraints == ("Max 1000 tokens", "JSON output only")


def test_soul_document_frozen() -> None:
    soul = SoulDocument(
        role="analyst",
        purpose="Analyse match results",
        principles=("Be thorough",),
        constraints=("No speculation",),
    )
    assert dataclasses.is_dataclass(soul)
    try:
        soul.role = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_soul_document_to_dict() -> None:
    soul = SoulDocument(
        role="coach",
        purpose="Update playbook",
        principles=("Incremental updates",),
        constraints=("Preserve working strategies",),
    )
    d = soul.to_dict()
    assert d == {
        "role": "coach",
        "purpose": "Update playbook",
        "principles": ["Incremental updates"],
        "constraints": ["Preserve working strategies"],
    }


def test_soul_document_from_dict() -> None:
    data = {
        "role": "architect",
        "purpose": "Design tools",
        "principles": ["Simplicity", "Reusability"],
        "constraints": ["No side effects"],
    }
    soul = SoulDocument.from_dict(data)
    assert soul.role == "architect"
    assert soul.purpose == "Design tools"
    assert soul.principles == ("Simplicity", "Reusability")
    assert soul.constraints == ("No side effects",)


def test_soul_document_to_prompt_section() -> None:
    soul = SoulDocument(
        role="competitor",
        purpose="Generate winning strategies",
        principles=("Always improve", "Never regress"),
        constraints=("Max 1000 tokens",),
    )
    md = soul.to_prompt_section()
    assert "## Soul: competitor" in md
    assert "**Purpose:** Generate winning strategies" in md
    assert "- Always improve" in md
    assert "- Never regress" in md
    assert "- Max 1000 tokens" in md


# ── AgentIdentity ────────────────────────────────────────────────────────────


def _make_identity(*, soul: SoulDocument | None = None, history: tuple[dict, ...] = ()) -> AgentIdentity:
    return AgentIdentity(
        role="competitor",
        soul=soul,
        traits=(
            IdentityTrait(name="advance_rate", value=0.750, trend=0.050, observations=10),
            IdentityTrait(name="cost_efficiency", value=0.900, trend=-0.010, observations=8),
        ),
        trust_tier="proven",
        total_generations=20,
        total_advances=15,
        created_at="2025-01-01T00:00:00+00:00",
        last_updated="2025-01-02T00:00:00+00:00",
        history=history,
    )


def test_agent_identity_construction() -> None:
    identity = _make_identity()
    assert identity.role == "competitor"
    assert identity.soul is None
    assert len(identity.traits) == 2
    assert identity.trust_tier == "proven"
    assert identity.total_generations == 20
    assert identity.total_advances == 15
    assert identity.history == ()


def test_agent_identity_frozen() -> None:
    identity = _make_identity()
    assert dataclasses.is_dataclass(identity)
    try:
        identity.role = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_agent_identity_to_dict() -> None:
    identity = _make_identity(history=({"gen": 1, "score": 50},))
    d = identity.to_dict()
    assert d["role"] == "competitor"
    assert d["soul"] is None
    assert len(d["traits"]) == 2
    assert d["traits"][0] == {"name": "advance_rate", "value": 0.750, "trend": 0.050, "observations": 10}
    assert d["trust_tier"] == "proven"
    assert d["total_generations"] == 20
    assert d["total_advances"] == 15
    assert d["created_at"] == "2025-01-01T00:00:00+00:00"
    assert d["last_updated"] == "2025-01-02T00:00:00+00:00"
    assert d["history"] == [{"gen": 1, "score": 50}]


def test_agent_identity_from_dict() -> None:
    data = {
        "role": "analyst",
        "soul": {
            "role": "analyst",
            "purpose": "Analyse matches",
            "principles": ["Be thorough"],
            "constraints": ["No speculation"],
        },
        "traits": [{"name": "advance_rate", "value": 0.6, "trend": 0.1, "observations": 5}],
        "trust_tier": "probation",
        "total_generations": 5,
        "total_advances": 3,
        "created_at": "2025-01-01T00:00:00+00:00",
        "last_updated": "2025-01-01T12:00:00+00:00",
        "history": [{"gen": 1}],
    }
    identity = AgentIdentity.from_dict(data)
    assert identity.role == "analyst"
    assert identity.soul is not None
    assert identity.soul.purpose == "Analyse matches"
    assert len(identity.traits) == 1
    assert identity.traits[0].name == "advance_rate"
    assert identity.trust_tier == "probation"
    assert identity.history == ({"gen": 1},)


def test_agent_identity_trait_found() -> None:
    identity = _make_identity()
    t = identity.trait("advance_rate")
    assert t is not None
    assert t.value == 0.750


def test_agent_identity_trait_missing() -> None:
    identity = _make_identity()
    assert identity.trait("nonexistent") is None


def test_agent_identity_to_prompt_section() -> None:
    identity = _make_identity()
    md = identity.to_prompt_section()
    assert "## Agent Identity: competitor" in md
    assert "**Trust Tier:** proven" in md
    assert "**Experience:** 20 generations, 15 advances" in md
    assert "| advance_rate | 0.750 | +0.050 |" in md
    assert "| cost_efficiency | 0.900 | -0.010 |" in md
    # No soul section when soul is None
    assert "## Soul:" not in md


def test_agent_identity_to_prompt_section_with_soul() -> None:
    soul = SoulDocument(
        role="competitor",
        purpose="Generate winning strategies",
        principles=("Always improve",),
        constraints=("Max 1000 tokens",),
    )
    identity = _make_identity(soul=soul)
    md = identity.to_prompt_section()
    assert "## Agent Identity: competitor" in md
    assert "## Soul: competitor" in md
    assert "**Purpose:** Generate winning strategies" in md
    assert "- Always improve" in md
    assert "- Max 1000 tokens" in md


def test_agent_identity_now_returns_iso_timestamp() -> None:
    ts = AgentIdentity.now()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None, "Timestamp must be timezone-aware"
