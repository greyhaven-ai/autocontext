"""Helpers for assembling ProductionTrace dicts from OpenAI requests/responses.

Uses the Foundation A emit SDK (``build_trace``) as the validation-and-shape
source of truth; this module only prepares kwargs. Redaction of error
messages happens here; PII stays out of the emit path.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from autocontext.production_traces.emit import build_trace

# Conservative secret-literal regex set: matches the shapes the
# production-traces redaction scanner looks for. Kept narrow on purpose —
# this is a best-effort last-line-of-defense, NOT the authoritative redactor.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xoxb-[A-Za-z0-9-]{10,}"),
]


def _redact(msg: str) -> str:
    for pat in _SECRET_PATTERNS:
        msg = pat.sub("<redacted>", msg)
    return msg


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure every message has the ``timestamp`` field required by the schema."""
    ts = _now_iso()
    normalized = []
    for msg in messages:
        if "timestamp" not in msg:
            msg = dict(msg)
            msg["timestamp"] = ts
        normalized.append(msg)
    return normalized


def _normalize_tool_calls(
    tool_calls: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Normalize OpenAI tool-call objects to the schema's ToolCall shape.

    OpenAI format: ``{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}``
    Schema format: ``{"toolName": "...", "args": {...}}``
    """
    if not tool_calls:
        return None
    result: list[dict[str, Any]] = []
    for tc in tool_calls:
        if "function" in tc:
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {"_raw": fn.get("arguments", "")}
            result.append({"toolName": fn.get("name", ""), "args": args})
        elif "toolName" in tc:
            # Already in schema format (e.g., from streaming accumulation)
            result.append(tc)
    return result or None


def build_request_snapshot(
    *,
    model: str,
    messages: list[dict[str, Any]],
    extra_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Package the pre-call request info for later trace assembly."""
    return {"model": model, "messages": messages, "extra": extra_kwargs}


def _map_usage(response_usage: dict[str, Any] | None) -> dict[str, Any]:
    if not response_usage:
        return {"tokensIn": 0, "tokensOut": 0}
    return {
        "tokensIn": int(response_usage.get("prompt_tokens", response_usage.get("input_tokens", 0))),
        "tokensOut": int(response_usage.get("completion_tokens", response_usage.get("output_tokens", 0))),
    }


def _identity_to_session(identity: dict[str, str]) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if "user_id_hash" in identity:
        out["userIdHash"] = identity["user_id_hash"]
    if "session_id_hash" in identity:
        out["sessionIdHash"] = identity["session_id_hash"]
    return out or None


def build_success_trace(
    *,
    request_snapshot: dict[str, Any],
    response_usage: dict[str, Any] | None,
    response_tool_calls: list[dict[str, Any]] | None,
    identity: dict[str, str],
    timing: dict[str, Any],
    env: dict[str, Any],
    source_info: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    return build_trace(
        provider="openai",
        model=request_snapshot["model"],
        messages=_normalize_messages(request_snapshot["messages"]),
        timing=timing,
        usage=_map_usage(response_usage),
        env=env,
        source=source_info,
        tool_calls=_normalize_tool_calls(response_tool_calls),
        session=_identity_to_session(identity),
        outcome={"label": "success"},
        trace_id=trace_id,
    )


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
    error_obj: dict[str, Any] = {
        "type": reason_key,
        "message": _redact(error_message),
    }
    if stack is not None:
        error_obj["stack"] = stack
    return build_trace(
        provider="openai",
        model=request_snapshot["model"],
        messages=_normalize_messages(request_snapshot["messages"]),
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
    accumulated_usage: dict[str, Any] | None,
    accumulated_tool_calls: list[dict[str, Any]] | None,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    return build_trace(
        provider="openai",
        model=request_snapshot["model"],
        messages=_normalize_messages(request_snapshot["messages"]),
        timing=timing,
        usage=_map_usage(accumulated_usage),
        env=env,
        source=source_info,
        tool_calls=_normalize_tool_calls(accumulated_tool_calls),
        session=_identity_to_session(identity),
        outcome=outcome,
        trace_id=trace_id,
    )
