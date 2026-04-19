"""Tests for the ergonomic non-raising validate variant + backward-compat shim.

The raising variant ``validate_production_trace`` is already covered in
``test_production_traces_contract.py``. These tests focus on the tuple-returning
``validate_production_trace_dict`` convenience wrapper.
"""

from __future__ import annotations

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


def test_validate_production_trace_dict_accepts_valid_and_returns_empty_errors() -> None:
    from autocontext.production_traces.validate import validate_production_trace_dict

    ok, errors = validate_production_trace_dict(_minimal_trace())
    assert ok is True
    assert errors == []


def test_validate_production_trace_dict_rejects_missing_required_field() -> None:
    from autocontext.production_traces.validate import validate_production_trace_dict

    data = _minimal_trace()
    del data["traceId"]
    ok, errors = validate_production_trace_dict(data)
    assert ok is False
    assert len(errors) >= 1
    # Error messages should mention the offending field path.
    joined = "\n".join(errors)
    assert "traceId" in joined


def test_validate_production_trace_dict_rejects_bad_role_with_field_pointer() -> None:
    from autocontext.production_traces.validate import validate_production_trace_dict

    data = _minimal_trace()
    data["messages"] = [
        {"role": "wizard", "content": "x", "timestamp": "2026-04-17T12:00:00.000Z"},
    ]
    ok, errors = validate_production_trace_dict(data)
    assert ok is False
    # Flattened pointer-like location: messages.0.role or similar.
    joined = "\n".join(errors)
    assert "messages" in joined and "role" in joined


def test_validate_production_trace_dict_rejects_non_dict_input() -> None:
    from autocontext.production_traces.validate import validate_production_trace_dict

    # Runtime-wrong input. The non-raising variant should not raise.
    ok, errors = validate_production_trace_dict("not a dict")  # type: ignore[arg-type]
    assert ok is False
    assert errors  # at least one error message


def test_exports_include_both_variants() -> None:
    # DDD surface check: the public __init__ exposes both validators.
    from autocontext.production_traces import (
        validate_production_trace,
        validate_production_trace_dict,
    )

    assert callable(validate_production_trace)
    assert callable(validate_production_trace_dict)
