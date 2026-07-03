"""builds the daemon's stage set from the charter (ingest, curate, advise real; train/evaluate no-op)."""

from __future__ import annotations

from pathlib import Path

from autocontext.ambient.advise import AdviseStage
from autocontext.ambient.charter import Charter
from autocontext.ambient.curate import CurateStage
from autocontext.ambient.datasets import DatasetStore
from autocontext.ambient.ingest import IngestStage
from autocontext.ambient.sources.agent_outputs import AgentOutputsSource
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
    datasets_dir: Path,
) -> dict[str, Stage]:
    sources: list[TraceSource] = []
    unsupported: list[tuple[str, str]] = []
    for spec in charter.sources:
        if not spec.enabled:
            continue
        if spec.kind == "autocontext":
            sources.append(NativeRunsSource(name=spec.name, runs_db_path=runs_db_path))
            # full output text rides its own source so it passes the redaction
            # gate at ingest; curate never reads the runs db directly
            sources.append(AgentOutputsSource(name=spec.name, runs_db_path=runs_db_path))
        elif spec.kind == "otel":
            sources.append(JsonlFeedSource(name=spec.name, feed_dir=otel_feed_dir))
        else:
            unsupported.append((spec.name, spec.kind))
    trace_store = TraceStore(db_path)
    dataset_store = DatasetStore(datasets_dir)
    stages: dict[str, Stage] = {name: NoOpStage(name=name) for name in STAGE_NAMES}
    stages["ingest"] = IngestStage(
        name="ingest",
        trace_store=trace_store,
        sources=sources,
        disk_quota_gb=charter.budgets.disk_quota_gb,
        unsupported=unsupported,
    )
    stages["curate"] = CurateStage(name="curate", trace_store=trace_store, dataset_store=dataset_store)
    stages["advise"] = AdviseStage(name="advise", trace_store=trace_store)
    return stages
