"""Tests for autocontext.harness.trust.types — TrustTier, TrustScore, TrustBudget."""

from __future__ import annotations

import dataclasses
from datetime import datetime

from autocontext.harness.trust.types import TrustBudget, TrustScore, TrustTier


def test_trust_tier_values() -> None:
    assert TrustTier.PROBATION.value == "probation"
    assert TrustTier.ESTABLISHED.value == "established"
    assert TrustTier.TRUSTED.value == "trusted"
    assert TrustTier.EXEMPLARY.value == "exemplary"


def test_trust_tier_ordering() -> None:
    # StrEnum members compare by string value
    assert TrustTier.PROBATION > TrustTier.ESTABLISHED  # "probation" > "established"
    assert TrustTier.ESTABLISHED < TrustTier.TRUSTED  # "established" < "trusted"
    assert TrustTier.TRUSTED > TrustTier.EXEMPLARY  # "trusted" > "exemplary"
    # Full lexicographic ordering: established < exemplary < probation < trusted
    tiers_sorted = sorted(TrustTier)
    assert tiers_sorted == [TrustTier.ESTABLISHED, TrustTier.EXEMPLARY, TrustTier.PROBATION, TrustTier.TRUSTED]


def test_trust_score_construction() -> None:
    score = TrustScore(
        role="competitor",
        tier=TrustTier.TRUSTED,
        raw_score=0.72,
        observations=15,
        confidence=0.9,
        last_updated="2025-06-01T00:00:00+00:00",
    )
    assert score.role == "competitor"
    assert score.tier == TrustTier.TRUSTED
    assert score.raw_score == 0.72
    assert score.observations == 15
    assert score.confidence == 0.9
    assert score.last_updated == "2025-06-01T00:00:00+00:00"


def test_trust_score_frozen() -> None:
    score = TrustScore(
        role="analyst",
        tier=TrustTier.PROBATION,
        raw_score=0.1,
        observations=2,
        confidence=0.2,
        last_updated="2025-06-01T00:00:00+00:00",
    )
    assert dataclasses.is_dataclass(score)
    try:
        score.role = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_trust_score_to_dict_round_trip() -> None:
    score = TrustScore(
        role="coach",
        tier=TrustTier.ESTABLISHED,
        raw_score=0.45,
        observations=10,
        confidence=0.75,
        last_updated="2025-06-01T12:00:00+00:00",
    )
    d = score.to_dict()
    restored = TrustScore.from_dict(d)
    assert restored == score


def test_trust_score_from_dict() -> None:
    data = {
        "role": "architect",
        "tier": "exemplary",
        "raw_score": 0.92,
        "observations": 50,
        "confidence": 0.99,
        "last_updated": "2025-07-01T00:00:00+00:00",
    }
    score = TrustScore.from_dict(data)
    assert score.role == "architect"
    assert score.tier == TrustTier.EXEMPLARY
    assert score.raw_score == 0.92
    assert score.observations == 50
    assert score.confidence == 0.99
    assert score.last_updated == "2025-07-01T00:00:00+00:00"


def test_trust_score_now_returns_iso_timestamp() -> None:
    ts = TrustScore.now()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None, "Timestamp must be timezone-aware"


def test_trust_budget_construction() -> None:
    budget = TrustBudget(
        tier=TrustTier.TRUSTED,
        max_retries=3,
        token_budget_multiplier=1.2,
        cadence_flexibility=True,
        model_upgrade_allowed=True,
    )
    assert budget.tier == TrustTier.TRUSTED
    assert budget.max_retries == 3
    assert budget.token_budget_multiplier == 1.2
    assert budget.cadence_flexibility is True
    assert budget.model_upgrade_allowed is True


def test_trust_budget_frozen() -> None:
    budget = TrustBudget(
        tier=TrustTier.PROBATION,
        max_retries=1,
        token_budget_multiplier=0.8,
        cadence_flexibility=False,
        model_upgrade_allowed=False,
    )
    assert dataclasses.is_dataclass(budget)
    try:
        budget.max_retries = 5  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_trust_budget_for_tier_probation() -> None:
    budget = TrustBudget.for_tier(TrustTier.PROBATION)
    assert budget.tier == TrustTier.PROBATION
    assert budget.max_retries == 1
    assert budget.token_budget_multiplier == 0.8
    assert budget.cadence_flexibility is False
    assert budget.model_upgrade_allowed is False


def test_trust_budget_for_tier_established() -> None:
    budget = TrustBudget.for_tier(TrustTier.ESTABLISHED)
    assert budget.tier == TrustTier.ESTABLISHED
    assert budget.max_retries == 2
    assert budget.token_budget_multiplier == 1.0
    assert budget.cadence_flexibility is False
    assert budget.model_upgrade_allowed is True


def test_trust_budget_for_tier_trusted() -> None:
    budget = TrustBudget.for_tier(TrustTier.TRUSTED)
    assert budget.tier == TrustTier.TRUSTED
    assert budget.max_retries == 3
    assert budget.token_budget_multiplier == 1.2
    assert budget.cadence_flexibility is True
    assert budget.model_upgrade_allowed is True


def test_trust_budget_for_tier_exemplary() -> None:
    budget = TrustBudget.for_tier(TrustTier.EXEMPLARY)
    assert budget.tier == TrustTier.EXEMPLARY
    assert budget.max_retries == 4
    assert budget.token_budget_multiplier == 1.5
    assert budget.cadence_flexibility is True
    assert budget.model_upgrade_allowed is True
