from __future__ import annotations

from pathlib import Path

from autocontext.ambient.advise import AdviseStage
from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.curate import CurateStage
from autocontext.ambient.evaluate import EvaluateStage
from autocontext.ambient.ingest import IngestStage
from autocontext.ambient.promote import PromoteStage
from autocontext.ambient.sources.agent_outputs import AgentOutputsSource
from autocontext.ambient.sources.jsonl_feed import JsonlFeedSource
from autocontext.ambient.sources.native import NativeRunsSource
from autocontext.ambient.stage import STAGE_NAMES
from autocontext.ambient.stage_factory import build_stages
from autocontext.ambient.train import TrainStage
from autocontext.harness.core.events import EventStreamEmitter


def _charter(sources: list[CharterSource], tier: str = "oss") -> Charter:
    return Charter(
        tier=tier,  # type: ignore[arg-type]
        sources=sources,
        targets=[
            CharterTarget(
                name="t1",
                kind="role",
                selector="competitor@grid_ctf",
                base_model="m",
                min_dataset_records=1,
                eval_suite="e",
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=1.0, window_hours=24, disk_quota_gb=7.0),
    )


def test_build_stages_wires_enabled_sources(tmp_path: Path) -> None:
    charter = _charter(
        [
            CharterSource(name="native", kind="autocontext"),
            CharterSource(name="feed", kind="otel"),
            CharterSource(name="off", kind="otel", enabled=False),
        ]
    )
    stages = build_stages(
        charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        runs_db_path=tmp_path / "runs.sqlite3",
        otel_feed_dir=tmp_path / "feed",
        datasets_dir=tmp_path / "datasets",
        registry_dir=tmp_path / "registry",
        usage_db=tmp_path / "usage.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        checkpoints_dir=tmp_path / "checkpoints",
        suites_dir=tmp_path / "suites",
    )
    assert set(stages.keys()) == set(STAGE_NAMES)
    ingest = stages["ingest"]
    assert isinstance(ingest, IngestStage)
    assert ingest.disk_quota_gb == 7.0
    kinds = [type(source) for source in ingest.sources]
    assert kinds == [NativeRunsSource, AgentOutputsSource, JsonlFeedSource]
    assert [source.kind for source in ingest.sources] == ["autocontext", "autocontext-outputs", "otel"]
    assert isinstance(stages["curate"], CurateStage)
    assert isinstance(stages["advise"], AdviseStage)
    assert isinstance(stages["evaluate"], EvaluateStage)
    assert isinstance(stages["promote"], PromoteStage)


def test_unsupported_kinds_emit_event_and_are_skipped(tmp_path: Path) -> None:
    events_path = tmp_path / "events.ndjson"
    charter = _charter(
        [CharterSource(name="native", kind="autocontext"), CharterSource(name="box", kind="full-box")],
        tier="hosted-box",
    )
    stages = build_stages(
        charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(events_path),
        runs_db_path=tmp_path / "runs.sqlite3",
        otel_feed_dir=tmp_path / "feed",
        datasets_dir=tmp_path / "datasets",
        registry_dir=tmp_path / "registry",
        usage_db=tmp_path / "usage.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        checkpoints_dir=tmp_path / "checkpoints",
        suites_dir=tmp_path / "suites",
    )
    ingest = stages["ingest"]
    assert isinstance(ingest, IngestStage)
    # the one enabled autocontext source registers both readers; the full-box source is skipped
    assert len(ingest.sources) == 2
    assert ingest.unsupported == [("box", "full-box")]
    # construction is event-silent; the announcement happens on first run
    assert not events_path.exists() or "ingest_source_unsupported" not in events_path.read_text(encoding="utf-8")


def test_autocontext_source_registers_generation_and_output_readers(tmp_path: Path) -> None:
    charter = _charter(sources=[CharterSource(name="native", kind="autocontext")])
    stages = build_stages(
        charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        runs_db_path=tmp_path / "runs.sqlite3",
        otel_feed_dir=tmp_path / "feed",
        datasets_dir=tmp_path / "datasets",
        registry_dir=tmp_path / "registry",
        usage_db=tmp_path / "usage.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        checkpoints_dir=tmp_path / "checkpoints",
        suites_dir=tmp_path / "suites",
    )
    ingest = stages["ingest"]
    kinds = sorted(source.kind for source in ingest.sources)  # type: ignore[attr-defined]
    assert kinds == ["autocontext", "autocontext-outputs"]


def test_build_stages_wires_real_curate_and_advise(tmp_path: Path) -> None:
    charter = _charter(sources=[CharterSource(name="native", kind="autocontext")])
    stages = build_stages(
        charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        runs_db_path=tmp_path / "runs.sqlite3",
        otel_feed_dir=tmp_path / "feed",
        datasets_dir=tmp_path / "datasets",
        registry_dir=tmp_path / "registry",
        usage_db=tmp_path / "usage.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        checkpoints_dir=tmp_path / "checkpoints",
        suites_dir=tmp_path / "suites",
    )
    assert isinstance(stages["curate"], CurateStage)
    assert isinstance(stages["advise"], AdviseStage)
    # one shared trace store: ingest writes and curate/advise read the same db
    assert stages["curate"].trace_store is stages["ingest"].trace_store  # type: ignore[attr-defined]
    assert stages["advise"].trace_store is stages["ingest"].trace_store  # type: ignore[attr-defined]
    assert isinstance(stages["train"], TrainStage)


def test_build_stages_wires_real_train_stage(tmp_path: Path) -> None:
    charter = _charter(sources=[CharterSource(name="native", kind="autocontext")])
    stages = build_stages(
        charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        runs_db_path=tmp_path / "runs.sqlite3",
        otel_feed_dir=tmp_path / "feed",
        datasets_dir=tmp_path / "datasets",
        registry_dir=tmp_path / "registry",
        usage_db=tmp_path / "usage.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        checkpoints_dir=tmp_path / "checkpoints",
        suites_dir=tmp_path / "suites",
    )
    assert isinstance(stages["train"], TrainStage)
    assert isinstance(stages["evaluate"], EvaluateStage)


def test_build_stages_wires_real_evaluate_and_promote_sharing_registry(tmp_path: Path) -> None:
    charter = _charter(sources=[CharterSource(name="native", kind="autocontext")])
    stages = build_stages(
        charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        runs_db_path=tmp_path / "runs.sqlite3",
        otel_feed_dir=tmp_path / "feed",
        datasets_dir=tmp_path / "datasets",
        registry_dir=tmp_path / "registry",
        usage_db=tmp_path / "usage.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        checkpoints_dir=tmp_path / "checkpoints",
        suites_dir=tmp_path / "suites",
    )
    evaluate = stages["evaluate"]
    promote = stages["promote"]
    train = stages["train"]
    assert isinstance(evaluate, EvaluateStage)
    assert isinstance(promote, PromoteStage)
    assert evaluate.suites_dir == tmp_path / "suites"
    # train, evaluate, and promote must share one ModelRegistry instance so a candidate
    # trained this cycle is the same object evaluate scores and promote activates
    assert evaluate.registry is train.registry  # type: ignore[attr-defined]
    assert promote.registry is train.registry  # type: ignore[attr-defined]
