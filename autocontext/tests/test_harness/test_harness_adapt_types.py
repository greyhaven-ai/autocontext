"""Tests for autocontext.harness.adapt.types — AdaptationStatus, AdaptationResult, AdaptationPolicy."""

from __future__ import annotations

import dataclasses
from datetime import datetime

from autocontext.harness.adapt.types import AdaptationPolicy, AdaptationResult, AdaptationStatus


def test_adaptation_status_values() -> None:
    assert AdaptationStatus.APPLIED.value == "applied"
    assert AdaptationStatus.SKIPPED_LOW_CONFIDENCE.value == "skipped_low_confidence"
    assert AdaptationStatus.SKIPPED_MAX_CHANGES.value == "skipped_max_changes"
    assert AdaptationStatus.SKIPPED_DISABLED.value == "skipped_disabled"
    assert AdaptationStatus.DRY_RUN.value == "dry_run"


def test_adaptation_result_construction() -> None:
    result = AdaptationResult(
        timestamp="2025-01-01T00:00:00+00:00",
        role="competitor",
        parameter="model",
        previous_value="claude-3-haiku",
        new_value="claude-3-sonnet",
        confidence=0.85,
        rationale="Higher advance rate expected",
        status=AdaptationStatus.APPLIED,
    )
    assert result.timestamp == "2025-01-01T00:00:00+00:00"
    assert result.role == "competitor"
    assert result.parameter == "model"
    assert result.previous_value == "claude-3-haiku"
    assert result.new_value == "claude-3-sonnet"
    assert result.confidence == 0.85
    assert result.rationale == "Higher advance rate expected"
    assert result.status == AdaptationStatus.APPLIED


def test_adaptation_result_frozen() -> None:
    result = AdaptationResult(
        timestamp="2025-01-01T00:00:00+00:00",
        role="analyst",
        parameter="cadence",
        previous_value="3",
        new_value="2",
        confidence=0.7,
        rationale="More frequent analysis needed",
        status=AdaptationStatus.DRY_RUN,
    )
    assert dataclasses.is_dataclass(result)
    try:
        result.role = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_adaptation_result_to_dict() -> None:
    result = AdaptationResult(
        timestamp="2025-01-01T00:00:00+00:00",
        role="architect",
        parameter="model",
        previous_value="claude-3-haiku",
        new_value="claude-3-opus",
        confidence=0.92,
        rationale="Better tooling output",
        status=AdaptationStatus.APPLIED,
    )
    d = result.to_dict()
    assert d == {
        "timestamp": "2025-01-01T00:00:00+00:00",
        "role": "architect",
        "parameter": "model",
        "previous_value": "claude-3-haiku",
        "new_value": "claude-3-opus",
        "confidence": 0.92,
        "rationale": "Better tooling output",
        "status": "applied",
    }


def test_adaptation_result_to_dict_roundtrip() -> None:
    original = AdaptationResult(
        timestamp="2025-06-15T12:30:00+00:00",
        role="coach",
        parameter="cadence",
        previous_value="3",
        new_value="1",
        confidence=0.65,
        rationale="Playbook stale",
        status=AdaptationStatus.SKIPPED_LOW_CONFIDENCE,
    )
    d = original.to_dict()
    restored = AdaptationResult.from_dict(d)
    assert restored == original


def test_adaptation_result_from_dict() -> None:
    data = {
        "timestamp": "2025-01-01T00:00:00+00:00",
        "role": "curator",
        "parameter": "model",
        "previous_value": "claude-3-sonnet",
        "new_value": "claude-3-opus",
        "confidence": 0.78,
        "rationale": "Quality gate needs stronger model",
        "status": "skipped_max_changes",
    }
    result = AdaptationResult.from_dict(data)
    assert result.timestamp == "2025-01-01T00:00:00+00:00"
    assert result.role == "curator"
    assert result.parameter == "model"
    assert result.previous_value == "claude-3-sonnet"
    assert result.new_value == "claude-3-opus"
    assert result.confidence == 0.78
    assert result.rationale == "Quality gate needs stronger model"
    assert result.status == AdaptationStatus.SKIPPED_MAX_CHANGES


def test_adaptation_result_now_returns_iso_timestamp() -> None:
    ts = AdaptationResult.now()
    # Should parse as a valid ISO 8601 datetime
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None, "Timestamp must be timezone-aware"


def test_adaptation_policy_defaults() -> None:
    policy = AdaptationPolicy()
    assert policy.enabled is False
    assert policy.min_confidence == 0.6
    assert policy.max_changes_per_cycle == 2
    assert policy.dry_run is False
    assert policy.allowed_parameters == frozenset({"model", "cadence"})


def test_adaptation_policy_custom_values() -> None:
    policy = AdaptationPolicy(
        enabled=True,
        min_confidence=0.8,
        max_changes_per_cycle=5,
        dry_run=True,
        allowed_parameters=frozenset({"model", "cadence", "temperature"}),
    )
    assert policy.enabled is True
    assert policy.min_confidence == 0.8
    assert policy.max_changes_per_cycle == 5
    assert policy.dry_run is True
    assert "temperature" in policy.allowed_parameters


def test_adaptation_policy_frozen() -> None:
    policy = AdaptationPolicy()
    assert dataclasses.is_dataclass(policy)
    try:
        policy.enabled = True  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_adaptation_policy_allowed_parameters_is_frozenset() -> None:
    policy = AdaptationPolicy()
    assert isinstance(policy.allowed_parameters, frozenset)
    # frozenset is immutable — no add method that works
    try:
        policy.allowed_parameters.add("temperature")  # type: ignore[attr-defined]
        raise AssertionError("Expected AttributeError")
    except AttributeError:
        pass
