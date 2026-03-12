"""Tests for autocontext.harness.audit.types — AuditCategory, AuditEntry."""

from __future__ import annotations

import dataclasses
from datetime import datetime

from autocontext.harness.audit.types import AuditCategory, AuditEntry


def test_audit_category_values() -> None:
    assert AuditCategory.LLM_CALL.value == "llm_call"
    assert AuditCategory.GATE_DECISION.value == "gate_decision"
    assert AuditCategory.COST_EVENT.value == "cost_event"
    assert AuditCategory.CONFIG_CHANGE.value == "config_change"
    assert AuditCategory.ERROR.value == "error"
    assert AuditCategory.SYSTEM.value == "system"


def test_audit_entry_construction() -> None:
    entry = AuditEntry(
        timestamp="2025-01-01T00:00:00+00:00",
        category=AuditCategory.LLM_CALL,
        actor="competitor",
        action="generate",
        detail="produced strategy",
        metadata={"model": "claude-3"},
    )
    assert entry.timestamp == "2025-01-01T00:00:00+00:00"
    assert entry.category == AuditCategory.LLM_CALL
    assert entry.actor == "competitor"
    assert entry.action == "generate"
    assert entry.detail == "produced strategy"
    assert entry.metadata == {"model": "claude-3"}


def test_audit_entry_frozen() -> None:
    entry = AuditEntry(
        timestamp="2025-01-01T00:00:00+00:00",
        category=AuditCategory.SYSTEM,
        actor="harness",
        action="start",
    )
    assert dataclasses.is_dataclass(entry)
    try:
        entry.actor = "other"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_audit_entry_defaults() -> None:
    entry = AuditEntry(
        timestamp="2025-01-01T00:00:00+00:00",
        category=AuditCategory.ERROR,
        actor="system",
        action="crash",
    )
    assert entry.detail == ""
    assert entry.metadata == {}


def test_audit_entry_to_dict() -> None:
    entry = AuditEntry(
        timestamp="2025-01-01T00:00:00+00:00",
        category=AuditCategory.GATE_DECISION,
        actor="gate",
        action="advance",
        detail="score improved",
        metadata={"delta": 15},
    )
    d = entry.to_dict()
    assert d == {
        "timestamp": "2025-01-01T00:00:00+00:00",
        "category": "gate_decision",
        "actor": "gate",
        "action": "advance",
        "detail": "score improved",
        "metadata": {"delta": 15},
    }


def test_audit_entry_from_dict() -> None:
    data = {
        "timestamp": "2025-01-01T00:00:00+00:00",
        "category": "cost_event",
        "actor": "billing",
        "action": "charge",
        "detail": "token usage",
        "metadata": {"tokens": 500},
    }
    entry = AuditEntry.from_dict(data)
    assert entry.timestamp == "2025-01-01T00:00:00+00:00"
    assert entry.category == AuditCategory.COST_EVENT
    assert entry.actor == "billing"
    assert entry.action == "charge"
    assert entry.detail == "token usage"
    assert entry.metadata == {"tokens": 500}


def test_audit_entry_now_returns_iso_timestamp() -> None:
    ts = AuditEntry.now()
    # Should parse as a valid ISO 8601 datetime
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None, "Timestamp must be timezone-aware"
