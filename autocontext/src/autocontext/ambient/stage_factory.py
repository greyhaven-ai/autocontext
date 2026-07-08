"""builds the daemon's stage set from the charter (all six stages ingest..promote are real)."""

from __future__ import annotations

from pathlib import Path

from autocontext.ambient.advise import AdviseStage
from autocontext.ambient.charter import Charter
from autocontext.ambient.curate import CurateStage
from autocontext.ambient.datasets import DatasetStore
from autocontext.ambient.evaluate import EvaluateStage
from autocontext.ambient.generation import build_candidate_generation_fn, generation_config_id
from autocontext.ambient.ingest import IngestStage
from autocontext.ambient.promote import PromoteStage
from autocontext.ambient.sources.agent_outputs import AgentOutputsSource
from autocontext.ambient.sources.contract import TraceSource
from autocontext.ambient.sources.jsonl_feed import JsonlFeedSource
from autocontext.ambient.sources.native import NativeRunsSource
from autocontext.ambient.stage import STAGE_NAMES, NoOpStage, Stage
from autocontext.ambient.trace_store import TraceStore
from autocontext.ambient.train import TrainStage
from autocontext.ambient.usage import UsageLedger
from autocontext.config import AppSettings
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.training.model_registry import ModelRegistry


def build_stages(
    charter: Charter,
    db_path: Path,
    emitter: EventStreamEmitter,
    runs_db_path: Path,
    otel_feed_dir: Path,
    datasets_dir: Path,
    registry_dir: Path,
    usage_db: Path,
    artifacts_dir: Path,
    checkpoints_dir: Path,
    suites_dir: Path,
    settings: AppSettings,
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
    # the NoOp comprehension seeds the dict in STAGE_NAMES order (which the daemon's run_cycle
    # iterates, so evaluate precedes promote); every slot is then replaced by its real stage.
    stages: dict[str, Stage] = {name: NoOpStage(name=name) for name in STAGE_NAMES}
    # one registry shared by train, evaluate, and promote: a candidate trained this cycle is the
    # same record evaluate scores and promote activates, so they must read and write one store.
    registry = ModelRegistry(registry_dir)
    stages["ingest"] = IngestStage(
        name="ingest",
        trace_store=trace_store,
        sources=sources,
        disk_quota_gb=charter.budgets.disk_quota_gb,
        unsupported=unsupported,
    )
    stages["curate"] = CurateStage(name="curate", trace_store=trace_store, dataset_store=dataset_store)
    stages["advise"] = AdviseStage(name="advise", trace_store=trace_store)
    stages["train"] = TrainStage(
        name="train",
        dataset_store=dataset_store,
        usage_ledger=UsageLedger(usage_db),
        registry=registry,
        artifacts_root=artifacts_dir,
        checkpoints_root=checkpoints_dir,
    )
    # opt-in real candidate generation (AC-891): a charter policy decision (the charter is the only
    # policy input). when enabled, serve each candidate's model and score its real output, and fold
    # the generation config into the eval fingerprint so changing it re-triggers evaluation rather
    # than skipping candidates on stale scores; otherwise leave generate_fn None (placeholder scoring).
    if charter.real_candidate_generation:
        generate_fn = build_candidate_generation_fn(settings)
        gen_config_id = generation_config_id(settings)
    else:
        generate_fn = None
        gen_config_id = ""
    stages["evaluate"] = EvaluateStage(
        name="evaluate",
        registry=registry,
        suites_dir=suites_dir,
        generate_fn=generate_fn,
        generation_config_id=gen_config_id,
    )
    stages["promote"] = PromoteStage(name="promote", registry=registry)
    return stages
