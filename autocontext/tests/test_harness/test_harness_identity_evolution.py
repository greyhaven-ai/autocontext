"""Tests for IdentityEvolver — trait derivation, history, and audit."""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.harness.audit.writer import AppendOnlyAuditWriter
from autocontext.harness.identity.evolution import MAX_HISTORY, IdentityEvolver
from autocontext.harness.identity.types import AgentIdentity, IdentityTrait, SoulDocument
from autocontext.harness.meta.types import RoleProfile
from autocontext.harness.trust.types import TrustScore, TrustTier


def _profile(
    role: str = "analyst",
    advance_rate: float = 0.5,
    gens: int = 10,
    cost: float = 0.01,
    cpa: float = 0.02,
    tokens: float = 0.5,
    latency: float = 500.0,
) -> RoleProfile:
    return RoleProfile(
        role=role,
        generations_observed=gens,
        advance_rate=advance_rate,
        mean_tokens=1000.0,
        mean_latency_ms=latency,
        mean_cost_per_gen=cost,
        cost_per_advance=cpa,
        token_efficiency=tokens,
    )


def _identity(
    role: str = "analyst",
    traits: tuple[IdentityTrait, ...] = (),
    trust_tier: str = "probation",
    soul: SoulDocument | None = None,
    history: tuple[dict, ...] = (),  # type: ignore[type-arg]
) -> AgentIdentity:
    return AgentIdentity(
        role=role,
        soul=soul,
        traits=traits,
        trust_tier=trust_tier,
        total_generations=0,
        total_advances=0,
        created_at="2025-01-01T00:00:00+00:00",
        last_updated="2025-01-01T00:00:00+00:00",
        history=history,
    )


def test_updates_traits_from_profile() -> None:
    evolver = IdentityEvolver()
    identity = _identity()
    profile = _profile(advance_rate=0.6, cost=0.05, cpa=0.10, tokens=0.8, latency=300.0)
    result = evolver.evolve(identity, profile)

    names = [t.name for t in result.traits]
    assert "advance_rate" in names
    assert "mean_cost_per_gen" in names
    assert "cost_per_advance" in names
    assert "token_efficiency" in names
    assert "mean_latency_ms" in names

    assert result.trait("advance_rate") is not None
    assert result.trait("advance_rate").value == 0.6  # type: ignore[union-attr]
    assert result.trait("mean_cost_per_gen").value == 0.05  # type: ignore[union-attr]
    assert result.trait("cost_per_advance").value == 0.10  # type: ignore[union-attr]
    assert result.trait("token_efficiency").value == 0.8  # type: ignore[union-attr]
    assert result.trait("mean_latency_ms").value == 300.0  # type: ignore[union-attr]


def test_computes_trend() -> None:
    evolver = IdentityEvolver()
    identity = _identity()
    p1 = _profile(advance_rate=0.4, cost=0.01, cpa=0.02, tokens=0.5, latency=500.0)
    evolved_once = evolver.evolve(identity, p1)

    p2 = _profile(advance_rate=0.7, cost=0.03, cpa=0.05, tokens=0.9, latency=400.0)
    evolved_twice = evolver.evolve(evolved_once, p2)

    assert evolved_twice.trait("advance_rate").trend == pytest.approx(0.3)  # type: ignore[union-attr]
    assert evolved_twice.trait("mean_cost_per_gen").trend == pytest.approx(0.02)  # type: ignore[union-attr]
    assert evolved_twice.trait("cost_per_advance").trend == pytest.approx(0.03)  # type: ignore[union-attr]
    assert evolved_twice.trait("token_efficiency").trend == pytest.approx(0.4)  # type: ignore[union-attr]
    assert evolved_twice.trait("mean_latency_ms").trend == pytest.approx(-100.0)  # type: ignore[union-attr]


def test_first_evolution_zero_trend() -> None:
    evolver = IdentityEvolver()
    identity = _identity()
    profile = _profile(advance_rate=0.5)
    result = evolver.evolve(identity, profile)

    for t in result.traits:
        assert t.trend == 0.0, f"Trait {t.name} should have zero trend on first evolution"


def test_preserves_soul() -> None:
    soul = SoulDocument(
        role="analyst",
        purpose="Analyze strategies",
        principles=("accuracy", "depth"),
        constraints=("no hallucination",),
    )
    evolver = IdentityEvolver()
    identity = _identity(soul=soul)
    result = evolver.evolve(identity, _profile())

    assert result.soul is not None
    assert result.soul.role == "analyst"
    assert result.soul.purpose == "Analyze strategies"
    assert result.soul.principles == ("accuracy", "depth")
    assert result.soul.constraints == ("no hallucination",)


def test_preserves_created_at() -> None:
    evolver = IdentityEvolver()
    identity = _identity()
    result = evolver.evolve(identity, _profile())
    assert result.created_at == "2025-01-01T00:00:00+00:00"


def test_updates_last_updated() -> None:
    evolver = IdentityEvolver()
    identity = _identity()
    result = evolver.evolve(identity, _profile())
    assert result.last_updated != "2025-01-01T00:00:00+00:00"
    assert result.last_updated > identity.last_updated


def test_updates_trust_tier() -> None:
    evolver = IdentityEvolver()
    identity = _identity(trust_tier="probation")
    trust = TrustScore(
        role="analyst",
        tier=TrustTier.TRUSTED,
        raw_score=0.7,
        observations=15,
        confidence=0.9,
        last_updated=TrustScore.now(),
    )
    result = evolver.evolve(identity, _profile(), trust_score=trust)
    assert result.trust_tier == "trusted"


def test_appends_history() -> None:
    evolver = IdentityEvolver()
    identity = _identity()
    result = evolver.evolve(identity, _profile())
    assert len(result.history) == 1
    assert result.history[0]["role"] == "analyst"
    assert result.history[0]["created_at"] == "2025-01-01T00:00:00+00:00"


def test_prunes_history_at_max() -> None:
    evolver = IdentityEvolver()
    # Create an identity that already has MAX_HISTORY entries in history.
    history = tuple(
        _identity(trust_tier=f"tier_{i}").to_dict() for i in range(MAX_HISTORY)
    )
    identity = _identity(history=history)
    assert len(identity.history) == MAX_HISTORY

    result = evolver.evolve(identity, _profile())
    assert len(result.history) == MAX_HISTORY
    # The oldest entry should have been pruned; the most recent original entry should still be present.
    assert result.history[-1]["role"] == "analyst"  # the just-appended snapshot


def test_audit_on_tier_change(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_path)
    evolver = IdentityEvolver(audit_writer=writer)

    identity = _identity(trust_tier="probation")
    trust = TrustScore(
        role="analyst",
        tier=TrustTier.ESTABLISHED,
        raw_score=0.45,
        observations=10,
        confidence=0.8,
        last_updated=TrustScore.now(),
    )
    evolver.evolve(identity, _profile(), trust_score=trust)

    entries = writer.read_all()
    assert len(entries) == 1
    assert entries[0].actor == "identity_evolver"
    assert entries[0].action == "tier_change:analyst"
    assert "probation -> established" in entries[0].detail
