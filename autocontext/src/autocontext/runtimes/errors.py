from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _format_seconds(value: object) -> str:
    try:
        seconds = float(str(value))
    except (TypeError, ValueError):
        return str(value)
    if seconds.is_integer():
        return f"{seconds:.0f}s"
    return f"{seconds:.2f}s"


def format_runtime_failure(runtime_name: str, metadata: Mapping[str, Any]) -> str:
    """Build a stable runtime failure message from AgentOutput metadata."""
    error = metadata.get("error")
    details: list[str] = []
    timeout_seconds = metadata.get("timeout_seconds")
    if error == "timeout" and timeout_seconds is not None:
        details.append(f"timed out after {_format_seconds(timeout_seconds)}")
    raw_detail = metadata.get("detail") or metadata.get("stderr") or ""
    if raw_detail:
        details.append(str(raw_detail))
    suffix = f" ({'; '.join(details)})" if details else ""
    return f"{runtime_name} failed: {error}{suffix}"
