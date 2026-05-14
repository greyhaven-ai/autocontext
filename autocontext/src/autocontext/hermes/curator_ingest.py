"""AC-704: ingest Hermes curator reports into autocontext ProductionTrace JSONL.

Read-only importer. Walks ``<hermes_home>/logs/curator/**/run.json``, maps
each curator run into a ProductionTrace, and writes JSONL to disk. The
parser is tolerant of missing fields (warnings, not hard failures) and
preserves curator metadata (counts, action lists, auto-transitions) for
downstream dataset exporters (AC-705).

Notes on the mapping:

- A curator run is not a chat conversation, so the ingester synthesizes a
  minimal system message describing the run. The ``include_llm_final``
  flag adds the curator's final summary as an assistant message; without
  it the LLM text stays out of the trace by default (privacy default).
- Curator action lists (consolidated / pruned / archived / added) and
  counts land in ``trace.metadata.curator_*`` so downstream consumers can
  filter without rederiving from raw run.json files.
- ``timing.startedAt`` / ``timing.endedAt`` / ``timing.latencyMs`` are
  derived from ``started_at + duration_seconds``. Missing start times fall
  back to file mtime to avoid hard failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from autocontext.production_traces.emit import build_trace


@dataclass(slots=True)
class IngestSummary:
    """What happened during a single ingest invocation."""

    runs_read: int = 0
    traces_written: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    output_path: Path | None = None


def ingest_curator_reports(
    *,
    home: Path,
    output: Path,
    since: str | None = None,
    limit: int | None = None,
    include_llm_final: bool = False,
    include_tool_args: bool = False,
) -> IngestSummary:
    """Walk a Hermes home, map curator runs to ProductionTrace JSONL.

    Args:
        home: Hermes home directory (the parent of ``logs/curator/``).
        output: JSONL output path. Created (with parents) if missing;
            overwritten if present. Always created even when there are no
            runs to write, so callers can rely on its existence.
        since: ISO-8601 timestamp; runs with ``started_at`` strictly
            before this value are skipped.
        limit: Maximum number of traces to write. The discovered runs
            are sorted oldest-first; ``limit`` takes the first N.
        include_llm_final: When True, attach the curator's
            ``llm_final_summary`` (if present) as an assistant message.
            Default False; the privacy posture is that LLM text stays
            out of the trace unless the operator opts in.
        include_tool_args: When True, attach raw tool-call args from
            ``run.json.tool_calls[]`` (if present). Default False to
            avoid leaking sensitive arguments.

    Returns:
        ``IngestSummary`` with counts and any per-run warnings.
    """
    summary = IngestSummary(output_path=output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Always create the output file, even when empty, so callers can rely
    # on its existence without an extra existence-check.
    output.write_text("", encoding="utf-8")

    curator_root = home / "logs" / "curator"
    if not curator_root.exists():
        return summary

    since_dt = _parse_iso(since) if since is not None else None
    run_paths = sorted(curator_root.rglob("run.json"))
    summary.runs_read = len(run_paths)

    traces: list[dict[str, Any]] = []
    for path in run_paths:
        if limit is not None and len(traces) >= limit:
            break

        raw_text = path.read_text(encoding="utf-8")
        try:
            data = _parse_json(raw_text)
        except ValueError as err:
            summary.skipped += 1
            summary.warnings.append(f"{path}: malformed JSON ({err})")
            continue

        started_at = _as_str(data.get("started_at"))
        if since_dt is not None and started_at is not None:
            parsed_started = _parse_iso(started_at)
            if parsed_started is not None and parsed_started < since_dt:
                continue

        trace = _curator_run_to_trace(
            data=data,
            run_path=path,
            include_llm_final=include_llm_final,
            include_tool_args=include_tool_args,
            warnings=summary.warnings,
        )
        traces.append(trace)

    if traces:
        with output.open("w", encoding="utf-8") as fh:
            for trace in traces:
                import json as _json

                fh.write(_json.dumps(trace, separators=(",", ":")) + "\n")
    summary.traces_written = len(traces)
    return summary


def _curator_run_to_trace(
    *,
    data: dict[str, Any],
    run_path: Path,
    include_llm_final: bool,
    include_tool_args: bool,
    warnings: list[str],
) -> dict[str, Any]:
    started_at = _as_str(data.get("started_at"))
    duration = _as_float(data.get("duration_seconds"))
    provider = _as_str(data.get("provider")) or "unknown"
    model = _as_str(data.get("model")) or "unknown"

    if started_at is None:
        warnings.append(f"{run_path}: missing started_at, using file mtime")
        started_at = datetime.fromtimestamp(run_path.stat().st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
    if duration is None:
        warnings.append(f"{run_path}: missing duration_seconds, using 0")
        duration = 0.0

    ended_at_dt = _add_seconds(started_at, duration)
    ended_at = ended_at_dt.isoformat().replace("+00:00", "Z") if ended_at_dt is not None else started_at

    counts = _as_dict(data.get("counts"))
    actions = {
        "consolidated": _as_str_list(data.get("consolidated")),
        "pruned": _as_str_list(data.get("pruned")),
        "archived": _as_str_list(data.get("archived")),
        "added": _as_str_list(data.get("added")),
    }
    auto_transitions = _as_dict(data.get("auto_transitions"))
    tool_call_counts = _as_dict(data.get("tool_call_counts"))

    summary_text = _build_summary_text(
        counts=counts,
        actions=actions,
        provider=provider,
        model=model,
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": summary_text,
            "timestamp": started_at,
        }
    ]
    if include_llm_final:
        llm_final = _as_str(data.get("llm_final_summary"))
        if llm_final:
            messages.append(
                {
                    "role": "assistant",
                    "content": llm_final,
                    "timestamp": ended_at,
                }
            )

    tool_calls = _build_tool_calls(data.get("tool_calls"), include_tool_args=include_tool_args)

    metadata: dict[str, Any] = {
        "curator_counts": counts,
        "curator_actions": actions,
        "auto_transitions": auto_transitions,
        "tool_call_counts": tool_call_counts,
        "source": "hermes.curator",
        "run_path": str(run_path),
    }

    return build_trace(
        provider=provider,
        model=model,
        messages=messages,
        timing={
            "startedAt": started_at,
            "endedAt": ended_at,
            "latencyMs": int(duration * 1000),
        },
        usage={"tokensIn": 0, "tokensOut": 0},
        env={"environmentTag": "dev", "appId": "hermes-curator"},
        tool_calls=tool_calls,
        metadata=metadata,
    )


def _build_summary_text(
    *,
    counts: dict[str, Any],
    actions: dict[str, list[str]],
    provider: str,
    model: str,
) -> str:
    parts = [
        f"Hermes curator run via {provider}/{model}.",
        f"Consolidated: {len(actions['consolidated'])}, pruned: {len(actions['pruned'])}, "
        f"archived: {len(actions['archived'])}, added: {len(actions['added'])}.",
    ]
    if counts:
        parts.append(f"Counts: {counts}.")
    return " ".join(parts)


def _build_tool_calls(raw: Any, *, include_tool_args: bool) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    calls: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        tool_name = entry.get("toolName")
        if not isinstance(tool_name, str):
            continue
        args = entry.get("args")
        call: dict[str, Any] = {
            "toolName": tool_name,
            "args": args if include_tool_args and isinstance(args, dict) else {},
        }
        if isinstance(entry.get("error"), str):
            call["error"] = entry["error"]
        calls.append(call)
    return calls


def _parse_json(text: str) -> dict[str, Any]:
    import json as _json

    parsed = _json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("run.json must be a JSON object")
    return parsed


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _add_seconds(started_at: str, seconds: float) -> datetime | None:
    parsed = _parse_iso(started_at)
    if parsed is None:
        return None
    return parsed + timedelta(seconds=seconds)


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = ["IngestSummary", "ingest_curator_reports"]
