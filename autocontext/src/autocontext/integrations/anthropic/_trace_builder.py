"""Helpers for assembling Anthropic-sourced ProductionTrace dicts."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from autocontext.integrations.anthropic._content import (
    extract_tool_uses,
    flatten_content,
)
from autocontext.production_traces.emit import build_trace

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{40,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xoxb-[A-Za-z0-9-]{10,}"),
]


def _redact(msg: str) -> str:
    for pat in _SECRET_PATTERNS:
        msg = pat.sub("<redacted>", msg)
    return msg


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_request_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten content and add timestamp to each message."""
    ts = _now_iso()
    result = []
    for msg in messages:
        m = dict(msg)
        m["content"] = flatten_content(m.get("content", ""))
        if "timestamp" not in m:
            m["timestamp"] = ts
        result.append(m)
    return result


def _map_usage(response_usage: dict[str, Any] | None) -> dict[str, Any]:
    if not response_usage:
        return {
            "tokensIn": 0,
            "tokensOut": 0,
            "providerUsage": {
                "inputTokens": 0,
                "cacheCreationInputTokens": 0,
                "cacheReadInputTokens": 0,
                "outputTokens": 0,
            },
        }
    # Use `or 0` to handle None values (Anthropic SDK model_dump() returns None for absent fields)
    input_tokens = int(response_usage.get("input_tokens") or 0)
    cache_create = int(response_usage.get("cache_creation_input_tokens") or 0)
    cache_read = int(response_usage.get("cache_read_input_tokens") or 0)
    output_tokens = int(response_usage.get("output_tokens") or 0)
    return {
        "tokensIn": input_tokens + cache_create + cache_read,
        "tokensOut": output_tokens,
        "providerUsage": {
            "inputTokens": input_tokens,
            "cacheCreationInputTokens": cache_create,
            "cacheReadInputTokens": cache_read,
            "outputTokens": output_tokens,
        },
    }


def build_request_snapshot(
    *,
    model: str,
    messages: list[dict[str, Any]],
    extra_kwargs: dict[str, Any],
) -> dict[str, Any]:
    return {"model": model, "messages": messages, "extra": extra_kwargs}


def _identity_to_session(identity: dict[str, str]) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if "user_id_hash" in identity:
        out["userIdHash"] = identity["user_id_hash"]
    if "session_id_hash" in identity:
        out["sessionIdHash"] = identity["session_id_hash"]
    return out or None


def _metadata_with_stop_reason(stop_reason: str | None) -> dict[str, Any] | None:
    if not stop_reason:
        return None
    return {"anthropicStopReason": stop_reason}


def build_success_trace(
    *,
    request_snapshot: dict[str, Any],
    response_content: str | list[dict[str, Any]],
    response_usage: dict[str, Any] | None,
    response_stop_reason: str | None,
    identity: dict[str, str],
    timing: dict[str, Any],
    env: dict[str, Any],
    source_info: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    ts = _now_iso()
    normalized_messages = _normalize_request_messages(request_snapshot["messages"])
    assistant_content = flatten_content(response_content)
    normalized_messages.append({
        "role": "assistant",
        "content": assistant_content,
        "timestamp": ts,
    })
    tool_calls = extract_tool_uses(response_content)
    usage_mapped = _map_usage(response_usage)
    kwargs: dict[str, Any] = {
        "provider": "anthropic",
        "model": request_snapshot["model"],
        "messages": normalized_messages,
        "timing": timing,
        "usage": usage_mapped,
        "env": env,
        "source": source_info,
        "session": _identity_to_session(identity),
        "outcome": {"label": "success"},
        "trace_id": trace_id,
    }
    if tool_calls is not None:
        kwargs["tool_calls"] = tool_calls
    metadata = _metadata_with_stop_reason(response_stop_reason)
    if metadata:
        kwargs["metadata"] = metadata
    return build_trace(**kwargs)


def build_failure_trace(
    *,
    request_snapshot: dict[str, Any],
    identity: dict[str, str],
    timing: dict[str, Any],
    env: dict[str, Any],
    source_info: dict[str, Any],
    trace_id: str,
    reason_key: str,
    error_message: str,
    stack: str | None,
) -> dict[str, Any]:
    error_obj: dict[str, Any] = {"type": reason_key, "message": _redact(error_message)}
    if stack is not None:
        error_obj["stack"] = stack
    return build_trace(
        provider="anthropic",
        model=request_snapshot["model"],
        messages=_normalize_request_messages(request_snapshot["messages"]),
        timing=timing,
        usage={"tokensIn": 0, "tokensOut": 0},
        env=env,
        source=source_info,
        session=_identity_to_session(identity),
        outcome={"label": "failure", "error": error_obj},
        trace_id=trace_id,
    )


def finalize_streaming_trace(
    *,
    request_snapshot: dict[str, Any],
    identity: dict[str, str],
    timing: dict[str, Any],
    env: dict[str, Any],
    source_info: dict[str, Any],
    trace_id: str,
    accumulated_content_blocks: dict[int, dict[str, Any]],
    accumulated_usage: dict[str, Any] | None,
    accumulated_stop_reason: str | None,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    ts = _now_iso()
    # Reconstruct linear block list from the index-keyed accumulator
    linear_blocks: list[dict[str, Any]] = []
    for idx in sorted(accumulated_content_blocks.keys()):
        block = accumulated_content_blocks[idx]
        btype = block.get("type")
        if btype == "text":
            linear_blocks.append({"type": "text", "text": block.get("buffer", "")})
        elif btype == "tool_use":
            linear_blocks.append({
                "type": "tool_use",
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input": block.get("finalized_input", {}),
            })
    normalized_messages = _normalize_request_messages(request_snapshot["messages"])
    normalized_messages.append({
        "role": "assistant",
        "content": flatten_content(linear_blocks),
        "timestamp": ts,
    })
    tool_calls = extract_tool_uses(linear_blocks)
    usage_mapped = _map_usage(accumulated_usage)
    kwargs: dict[str, Any] = {
        "provider": "anthropic",
        "model": request_snapshot["model"],
        "messages": normalized_messages,
        "timing": timing,
        "usage": usage_mapped,
        "env": env,
        "source": source_info,
        "session": _identity_to_session(identity),
        "outcome": outcome,
        "trace_id": trace_id,
    }
    if tool_calls is not None:
        kwargs["tool_calls"] = tool_calls
    metadata = _metadata_with_stop_reason(accumulated_stop_reason)
    if metadata:
        kwargs["metadata"] = metadata
    return build_trace(**kwargs)
