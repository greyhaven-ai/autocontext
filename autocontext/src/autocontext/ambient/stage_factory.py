"""builds the daemon's stage set from the charter (real ingest, no-op others for now)."""

from __future__ import annotations

from pathlib import Path

from autocontext.ambient.charter import Charter
from autocontext.ambient.ingest import IngestStage
from autocontext.ambient.sources.contract import TraceSource
from autocontext.ambient.sources.jsonl_feed import JsonlFeedSource
from autocontext.ambient.sources.native import NativeRunsSource
from autocontext.ambient.stage import STAGE_NAMES, NoOpStage, Stage
from autocontext.ambient.trace_store import TraceStore
from autocontext.harness.core.events import EventStreamEmitter


def build_stages(
    charter: Charter,
    db_path: Path,
    emitter: EventStreamEmitter,
    runs_db_path: Path,
    otel_feed_dir: Path,
) -> dict[str, Stage]:
    sources: list[TraceSource] = []
    unsupported: list[tuple[str, str]] = []
    for spec in charter.sources:
        if not spec.enabled:
            continue
        if spec.kind == "autocontext":
            sources.append(NativeRunsSource(name=spec.name, runs_db_path=runs_db_path))
        elif spec.kind == "otel":
            sources.append(JsonlFeedSource(name=spec.name, feed_dir=otel_feed_dir))
        else:
            unsupported.append((spec.name, spec.kind))
    stages: dict[str, Stage] = {name: NoOpStage(name=name) for name in STAGE_NAMES}
    stages["ingest"] = IngestStage(
        name="ingest",
        trace_store=TraceStore(db_path),
        sources=sources,
        disk_quota_gb=charter.budgets.disk_quota_gb,
        unsupported=unsupported,
    )
    return stages
