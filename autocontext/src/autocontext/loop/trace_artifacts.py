"""Trace artifact helpers for generation runs."""

from __future__ import annotations

import json
from pathlib import Path

from autocontext.analytics.artifact_rendering import render_timeline_inspection_html, timeline_inspection_view
from autocontext.analytics.run_trace import RunTrace
from autocontext.analytics.timeline_inspector import StateInspector, TimelineBuilder


def persist_run_inspection(trace: RunTrace, analytics_root: Path, trace_path: Path) -> None:
    """Persist operator-facing inspection artifacts derived from a run trace."""
    inspection_dir = analytics_root / "inspections"
    inspection_dir.mkdir(parents=True, exist_ok=True)
    inspector = StateInspector()
    builder = TimelineBuilder()
    generation_indices = sorted({
        event.generation_index for event in trace.events if event.generation_index is not None
    })
    payload = {
        "trace_id": trace.trace_id,
        "run_id": trace.run_id,
        "trace_path": str(trace_path),
        "created_at": trace.created_at,
        "run_inspection": inspector.inspect_run(trace).model_dump(),
        "generation_inspections": [
            inspector.inspect_generation(trace, generation_index).model_dump()
            for generation_index in generation_indices
        ],
        "timeline_summary": [entry.to_dict() for entry in builder.build_summary(trace)],
        "failure_paths": [
            [event.event_id for event in path]
            for path in inspector.find_failure_paths(trace)
        ],
        "recovery_paths": [
            [event.event_id for event in path]
            for path in inspector.find_recovery_paths(trace)
        ],
    }
    (inspection_dir / f"{trace.trace_id}.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    (inspection_dir / f"{trace.trace_id}.html").write_text(
        render_timeline_inspection_html(timeline_inspection_view(trace)),
        encoding="utf-8",
    )
