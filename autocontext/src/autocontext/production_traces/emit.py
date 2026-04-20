"""Customer-facing emit helpers for ``ProductionTrace`` documents.

DDD discipline: argument names on :func:`build_trace` mirror spec §4
``ProductionTrace`` field names (translated to Python snake_case). No synonyms,
no novel vocabulary.

DRY discipline: the generated Pydantic model in ``contract.models`` is the
single source of truth for field shapes. This module assembles a ``dict`` and
runs it through ``ProductionTrace.model_validate(...)`` as a safety net. Field
types and shapes are never redefined here.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from ulid import ULID

from autocontext.production_traces.contract.models import ProductionTrace

# Directory layout from spec §6.1.
_ROOT_DIR = ".autocontext"
_PT_DIR = "production-traces"
_INCOMING = "incoming"
_REGISTRY_ENV_VAR = "AUTOCONTEXT_REGISTRY_PATH"


def _sdk_version() -> str:
    try:
        return _pkg_version("autocontext")
    except PackageNotFoundError:  # pragma: no cover - editable install fallback
        return "0.0.0"


def _default_source() -> dict[str, Any]:
    return {
        "emitter": "sdk",
        "sdk": {"name": "autocontext-py", "version": _sdk_version()},
    }


def build_trace(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    timing: dict[str, Any],
    usage: dict[str, Any],
    env: dict[str, Any],
    source: dict[str, Any] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    session: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
    feedback_refs: list[dict[str, Any]] | None = None,
    links: dict[str, Any] | None = None,
    redactions: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Construct a ``ProductionTrace`` dict, validate via Pydantic, and return it.

    Argument names mirror spec §4 ``ProductionTrace`` fields verbatim (Python
    snake_case ↔ JSON camelCase translation). Dict *values* carry the
    camelCase JSON field names the schema expects (``startedAt``, ``tokensIn``,
    etc.).

    Returns a plain ``dict`` (not a Pydantic instance) so customer code can
    mutate and merge freely. The Pydantic validation at the end is a safety
    net — invalid inputs raise :class:`pydantic.ValidationError` at
    construction time rather than at ingest time.
    """
    trace: dict[str, Any] = {
        "schemaVersion": "1.0",
        "traceId": trace_id if trace_id is not None else str(ULID()),
        "source": source if source is not None else _default_source(),
        "provider": {"name": provider},
        "model": model,
        "env": env,
        "messages": messages,
        "toolCalls": tool_calls if tool_calls is not None else [],
        "timing": timing,
        "usage": usage,
        "feedbackRefs": feedback_refs if feedback_refs is not None else [],
        "links": links if links is not None else {},
        "redactions": redactions if redactions is not None else [],
    }
    if session is not None:
        trace["session"] = session
    if outcome is not None:
        trace["outcome"] = outcome
    if metadata is not None:
        trace["metadata"] = metadata

    # Pydantic safety net — raises ValidationError on bad input. Re-dumping via
    # model_dump would strip fields; we validate and discard the parsed model,
    # keeping the caller's original dict (so dict values remain mutable).
    ProductionTrace.model_validate(trace)
    return trace


def write_jsonl(
    traces: dict[str, Any] | list[dict[str, Any]],
    cwd: str | Path | None = None,
    batch_id: str | None = None,
) -> Path:
    """Write one or more traces to
    ``<cwd>/.autocontext/production-traces/incoming/<YYYY-MM-DD>/<batch-ulid>.jsonl``.

    Resolution order for ``cwd``:

    1. Explicit ``cwd`` argument.
    2. ``AUTOCONTEXT_REGISTRY_PATH`` environment variable.
    3. Current working directory.

    The date partition is the UTC date of the first trace's
    ``timing.startedAt`` (falling back to ``datetime.now(UTC)`` if the first
    trace lacks timing). Batch id defaults to a fresh ULID.

    Returns the absolute path of the written file.
    """
    if isinstance(traces, dict):
        trace_list = [traces]
    else:
        trace_list = list(traces)

    base = _resolve_cwd(cwd)
    date_partition = _partition_date(trace_list)
    batch = batch_id if batch_id is not None else str(ULID())

    out_dir = base / _ROOT_DIR / _PT_DIR / _INCOMING / date_partition
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{batch}.jsonl"

    with out_path.open("w", encoding="utf-8") as fh:
        for trace in trace_list:
            fh.write(json.dumps(trace, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")

    return out_path.resolve()


class TraceBatch:
    """In-memory accumulator for high-throughput emit paths.

    Usage::

        batch = TraceBatch()
        for event in stream:
            batch.add(build_trace(...))
        batch.flush()  # writes accumulated traces as one file

    Accumulates without bounds — flush regularly in long-running processes.
    """

    def __init__(self) -> None:
        self._traces: list[dict[str, Any]] = []

    def add(self, trace: dict[str, Any]) -> None:
        self._traces.append(trace)

    def flush(self, cwd: str | Path | None = None) -> Path | None:
        if not self._traces:
            return None
        path = write_jsonl(self._traces, cwd=cwd)
        self._traces = []
        return path

    def __len__(self) -> int:
        return len(self._traces)


# ---- internals ----


def _resolve_cwd(cwd: str | Path | None) -> Path:
    if cwd is not None:
        return Path(cwd).resolve()
    env_val = os.environ.get(_REGISTRY_ENV_VAR)
    if env_val:
        return Path(env_val).resolve()
    return Path.cwd().resolve()


def _partition_date(traces: list[dict[str, Any]]) -> str:
    if traces:
        timing = traces[0].get("timing") or {}
        started = timing.get("startedAt")
        if isinstance(started, str):
            parsed = _parse_iso_utc(started)
            if parsed is not None:
                return parsed.strftime("%Y-%m-%d")
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _parse_iso_utc(value: str) -> datetime | None:
    # Accept both "...Z" and explicit offsets. ``fromisoformat`` in 3.11+
    # handles most shapes; "Z" needs a swap on older stdlib versions.
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


__all__ = ["TraceBatch", "build_trace", "write_jsonl"]
