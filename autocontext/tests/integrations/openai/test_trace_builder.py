"""Tests for the trace-builder helper that assembles dicts from request/response."""
from __future__ import annotations

from autocontext.integrations.openai._trace_builder import (
    build_failure_trace,
    build_request_snapshot,
    build_success_trace,
    finalize_streaming_trace,
)


def test_build_request_snapshot_basic() -> None:
    req = build_request_snapshot(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        extra_kwargs={"temperature": 0.5},
    )
    assert req["model"] == "gpt-4o"
    assert req["messages"] == [{"role": "user", "content": "hi"}]
    assert req["extra"] == {"temperature": 0.5}


_USER_HASH = "a" * 64   # valid 64-char hex string
_SESSION_HASH = "b" * 64  # valid 64-char hex string


def test_build_success_trace_minimal() -> None:
    trace = build_success_trace(
        request_snapshot={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "extra": {}},
        response_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        response_tool_calls=None,
        identity={"user_id_hash": _USER_HASH, "session_id_hash": _SESSION_HASH},
        timing={"startedAt": "2026-04-21T00:00:00Z", "endedAt": "2026-04-21T00:00:01Z", "latencyMs": 1000},
        env={"environmentTag": "test", "appId": "myapp"},
        source_info={"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": "0.0.0"}},
        trace_id="01HN0000000000000000000001",
    )
    assert trace["schemaVersion"] == "1.0"
    assert trace["traceId"] == "01HN0000000000000000000001"
    assert trace["provider"]["name"] == "openai"
    assert trace["model"] == "gpt-4o"
    assert trace["usage"] == {"tokensIn": 10, "tokensOut": 5}
    assert trace["outcome"] == {"label": "success"}
    assert trace["session"]["userIdHash"] == _USER_HASH
    assert trace["session"]["sessionIdHash"] == _SESSION_HASH


def test_build_success_trace_with_tool_calls() -> None:
    tc = [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": '{"x":1}'}}]
    trace = build_success_trace(
        request_snapshot={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "extra": {}},
        response_usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        response_tool_calls=tc,
        identity={},
        timing={"startedAt": "2026-04-21T00:00:00Z", "endedAt": "2026-04-21T00:00:01Z", "latencyMs": 0},
        env={"environmentTag": "test", "appId": "a"},
        source_info={"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": "0.0.0"}},
        trace_id="01HN0000000000000000000002",
    )
    # Tool calls are normalized from OpenAI format to schema format
    assert len(trace["toolCalls"]) == 1
    assert trace["toolCalls"][0]["toolName"] == "f"


def test_build_failure_trace() -> None:
    trace = build_failure_trace(
        request_snapshot={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "extra": {}},
        identity={},
        timing={"startedAt": "2026-04-21T00:00:00Z", "endedAt": "2026-04-21T00:00:01Z", "latencyMs": 0},
        env={"environmentTag": "test", "appId": "a"},
        source_info={"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": "0.0.0"}},
        trace_id="01HN0000000000000000000003",
        reason_key="rateLimited",
        error_message="too many requests",
        stack=None,
    )
    assert trace["outcome"]["label"] == "failure"
    assert trace["outcome"]["error"]["type"] == "rateLimited"
    assert trace["outcome"]["error"]["message"] == "too many requests"


def test_finalize_streaming_trace_partial_abandoned() -> None:
    trace = finalize_streaming_trace(
        request_snapshot={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "extra": {}},
        identity={},
        timing={"startedAt": "2026-04-21T00:00:00Z", "endedAt": "2026-04-21T00:00:01Z", "latencyMs": 0},
        env={"environmentTag": "test", "appId": "a"},
        source_info={"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": "0.0.0"}},
        trace_id="01HN0000000000000000000004",
        accumulated_usage=None,
        accumulated_tool_calls=None,
        outcome={"label": "partial", "reasoning": "abandonedStream"},
    )
    assert trace["outcome"]["label"] == "partial"
    assert trace["outcome"]["reasoning"] == "abandonedStream"


def test_build_failure_trace_redacts_secret_in_message() -> None:
    trace = build_failure_trace(
        request_snapshot={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "extra": {}},
        identity={},
        timing={"startedAt": "2026-04-21T00:00:00Z", "endedAt": "2026-04-21T00:00:01Z", "latencyMs": 0},
        env={"environmentTag": "test", "appId": "a"},
        source_info={"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": "0.0.0"}},
        trace_id="01HN0000000000000000000005",
        reason_key="authentication",
        error_message="invalid API key: sk-abc123XYZ789defghi012345",
        stack=None,
    )
    assert "sk-abc123XYZ789defghi012345" not in trace["outcome"]["error"]["message"]
