"""Tests for autocontext.production_traces.contract — Pydantic models and validate entry point."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocontext.production_traces import validate_production_trace
from autocontext.production_traces.contract import (
    AppId,
    ProductionTrace,
    ProductionTraceId,
    UserIdHash,
)
from autocontext.production_traces.contract.branded_ids import (
    EnvironmentTag,
    Scenario,
    SessionIdHash,
)

VALID_TRACE_ID = "01KFDQ9XZ3M7RT2V8K1PHY4BNC"


def _minimal_trace() -> dict:
    return {
        "schemaVersion": "1.0",
        "traceId": VALID_TRACE_ID,
        "source": {"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": "0.4.3"}},
        "provider": {"name": "anthropic"},
        "model": "claude-sonnet-4-20250514",
        "env": {"environmentTag": "production", "appId": "my-app"},
        "messages": [
            {"role": "user", "content": "hello", "timestamp": "2026-04-17T12:00:00.000Z"},
        ],
        "toolCalls": [],
        "timing": {
            "startedAt": "2026-04-17T12:00:00.000Z",
            "endedAt": "2026-04-17T12:00:01.000Z",
            "latencyMs": 1000,
        },
        "usage": {"tokensIn": 10, "tokensOut": 5},
        "feedbackRefs": [],
        "links": {},
        "redactions": [],
    }


def test_validate_production_trace_accepts_minimal_valid_input() -> None:
    trace = validate_production_trace(_minimal_trace())
    assert isinstance(trace, ProductionTrace)
    assert trace.traceId == VALID_TRACE_ID
    assert trace.schemaVersion == "1.0"
    assert trace.provider.name == "anthropic"


def test_validate_production_trace_rejects_missing_required_field() -> None:
    data = _minimal_trace()
    del data["traceId"]
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_validate_production_trace_rejects_invalid_ulid() -> None:
    data = _minimal_trace()
    data["traceId"] = "01kfdq9xz3m7rt2v8k1phy4bnc"  # lowercase
    with pytest.raises(ValidationError):
        validate_production_trace(data)

    data["traceId"] = "01KFDQ9XZ3M7RT2V8K1PHY4BNI"  # contains forbidden 'I'
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_validate_production_trace_rejects_unknown_provider_name() -> None:
    data = _minimal_trace()
    data["provider"] = {"name": "aliens"}
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_validate_production_trace_rejects_empty_messages() -> None:
    data = _minimal_trace()
    data["messages"] = []
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_validate_production_trace_rejects_bad_role() -> None:
    data = _minimal_trace()
    data["messages"] = [
        {"role": "wizard", "content": "x", "timestamp": "2026-04-17T12:00:00.000Z"},
    ]
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_validate_production_trace_accepts_optional_fields() -> None:
    data = _minimal_trace()
    data["session"] = {
        "userIdHash": "a" * 64,
        "sessionIdHash": "b" * 64,
        "requestId": "req-123",
    }
    data["outcome"] = {"label": "success", "score": 0.9}
    data["feedbackRefs"] = [
        {"kind": "thumbs", "submittedAt": "2026-04-17T12:05:00.000Z", "ref": "fb-1"},
    ]
    data["links"] = {"scenarioId": "grid_ctf", "runId": "run-42"}
    data["redactions"] = [
        {
            "path": "/messages/0/content",
            "reason": "pii-email",
            "detectedBy": "ingestion",
            "detectedAt": "2026-04-17T12:00:02.000Z",
        }
    ]
    data["metadata"] = {"customer": "acme-corp"}
    trace = validate_production_trace(data)
    assert trace.session is not None and trace.session.requestId == "req-123"
    assert trace.outcome is not None and trace.outcome.label == "success"
    assert trace.links.scenarioId == "grid_ctf"


def test_validate_production_trace_rejects_bad_user_id_hash() -> None:
    data = _minimal_trace()
    data["session"] = {"userIdHash": "A" * 64}  # uppercase not allowed
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_validate_production_trace_rejects_bad_app_id() -> None:
    data = _minimal_trace()
    data["env"]["appId"] = "My App With Spaces"
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_validate_production_trace_rejects_negative_tokens() -> None:
    data = _minimal_trace()
    data["usage"] = {"tokensIn": -1, "tokensOut": 0}
    with pytest.raises(ValidationError):
        validate_production_trace(data)


def test_validate_production_trace_round_trips_via_model_dump() -> None:
    data = _minimal_trace()
    trace = validate_production_trace(data)
    # Pydantic's default dump drops None-valued optional fields — `mode='json'`
    # gives a JSON-serializable dict. Round-trip through validate should succeed.
    redumped = trace.model_dump(mode="json", exclude_none=True)
    reparsed = validate_production_trace(redumped)
    assert reparsed.model_dump(mode="json", exclude_none=True) == redumped


def test_branded_ids_annotations_are_aliases() -> None:
    # Smoke-check: these are TypeAlias values at runtime, not classes.
    # Just confirm the names are importable and distinct.
    assert ProductionTraceId is not UserIdHash
    assert AppId is not SessionIdHash
    assert EnvironmentTag is not Scenario
