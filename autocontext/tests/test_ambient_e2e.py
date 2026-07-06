"""miniature end-to-end acceptance: ingest -> curate -> advise -> train -> evaluate -> promote -> serving.

This is the V1 acceptance in miniature. It seeds a runs sqlite db with completed frontier competitor
traces, builds a full-autonomy charter and a tiny eval suite, then drives the real stage set (built by
build_stages so train/evaluate/promote share one ModelRegistry and one TraceStore) in cycle order.
Every non-deterministic seam is injected: the training backend is monkeypatched to a fake that writes a
checkpoint dir instead of fine-tuning, and the evaluate stage's anchor judge + drift probe are replaced
with deterministic fakes. No real fine-tune, no LLM, no network, no sleeps.

The assertion proves the whole loop: a candidate is trained, scored above threshold, promoted to active,
and serving resolution for the target routes to that exact artifact from the registry (not the fallback).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import autocontext.ambient.train as train_mod
from autocontext.ambient.charter import Charter, CharterAnchor, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.evaluate import EvaluateStage
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.serving import resolve_active_serving
from autocontext.ambient.stage import STAGE_NAMES, StageContext
from autocontext.ambient.stage_factory import build_stages
from autocontext.ambient.training_backend import TrainOutcome
from autocontext.execution.bias_probes import BiasProbeResult
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.training.model_registry import ModelRegistry

_NOW = "2026-07-06T12:00:00+00:00"
_ANCHOR = "claude-sonnet-5"
_BACKEND = "mlx"
_TARGET = "competitor-grid_ctf"

_RUNS_SCHEMA = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, target_generations INTEGER NOT NULL,
    executor_mode TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE generations (
    run_id TEXT NOT NULL, generation_index INTEGER NOT NULL, mean_score REAL NOT NULL,
    best_score REAL NOT NULL, gate_decision TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, generation_index)
);
CREATE TABLE agent_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, generation_index INTEGER NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _seed_runs_db(db: Path, generations: int) -> None:
    """Seed one completed grid_ctf run with `generations` completed competitor outputs.

    The agent_outputs source hands these to curate as frontier-provenance agent_output traces; each
    completed competitor row becomes one eligible training record for the competitor target.
    """
    conn = sqlite3.connect(db)
    conn.executescript(_RUNS_SCHEMA)
    conn.execute(
        "INSERT INTO runs (run_id, scenario, target_generations, executor_mode, status) "
        "VALUES ('r1', 'grid_ctf', ?, 'local', 'completed')",
        (generations,),
    )
    for index in range(generations):
        conn.execute(
            "INSERT INTO generations (run_id, generation_index, mean_score, best_score, gate_decision, status) "
            "VALUES ('r1', ?, 0.8, 0.9, 'advance', 'completed')",
            (index,),
        )
        conn.execute(
            "INSERT INTO agent_outputs (run_id, generation_index, role, content) VALUES ('r1', ?, 'competitor', ?)",
            (index, f"capture the flag via corner control, plan v{index}"),
        )
    conn.commit()
    conn.close()


def _charter() -> Charter:
    return Charter(
        tier="oss",
        autonomy="full",  # train and promote are both autonomous
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=[
            CharterTarget(
                name=_TARGET,
                kind="role",
                selector="competitor@grid_ctf",
                base_model="tiny",
                min_dataset_records=2,
                eval_suite="e2e_holdout",
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=10.0, window_hours=24, disk_quota_gb=1.0),
        anchor=CharterAnchor(provider="anthropic", model=_ANCHOR, rubric="Score 0 to 1."),
    )


def _write_suite(suites_dir: Path) -> None:
    suites_dir.mkdir(parents=True, exist_ok=True)
    cases = [
        {"prompt": "hold the two corners nearest your base", "reference": "corner control"},
        {"prompt": "when should you rush the flag", "reference": "after securing the center"},
    ]
    (suites_dir / "e2e_holdout.jsonl").write_text("\n".join(json.dumps(case) for case in cases) + "\n", encoding="utf-8")


def _fake_run_training(backend_name: str, request: Any) -> TrainOutcome:
    """Stand in for a real fine-tune: write a checkpoint dir, return a tiny-budget outcome."""
    checkpoint = request.output_dir / "adapters"
    checkpoint.mkdir(parents=True, exist_ok=True)
    (checkpoint / "adapter_config.json").write_text("{}", encoding="utf-8")
    return TrainOutcome(
        checkpoint_path=checkpoint,
        backend=backend_name,
        metrics={"avg_score": 0.85, "valid_rate": 1.0, "num_records": 3.0, "training_seconds": 36.0},
        gpu_hours=0.01,  # well within the 10h window
    )


class _FixedScorer:
    def __init__(self, value: float) -> None:
        self.value = value

    def score(self, prompt: str, output: str) -> float:
        return self.value


def _clean_probe(_anchor: CharterAnchor) -> BiasProbeResult:
    # magnitude 0.0 <= the 0.2 default tolerance, so the candidate reads drift-clean
    return BiasProbeResult(probe_type="position", detected=False, magnitude=0.0, details="")


def _ctx(tmp_path: Path, charter: Charter) -> StageContext:
    return StageContext(
        charter=charter,
        queue=AmbientQueue(tmp_path / "q.sqlite3"),
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
    )


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_miniature_ingest_through_serving(tmp_path: Path, monkeypatch: Any) -> None:
    runs_db = tmp_path / "runs.sqlite3"
    _seed_runs_db(runs_db, generations=3)
    _write_suite(tmp_path / "suites")
    charter = _charter()

    # a fixed backend name + a checkpoint-writing fake training run: no real fine-tune, deterministic budget.
    monkeypatch.setattr(train_mod, "select_backend", lambda method: _BACKEND)
    monkeypatch.setattr(train_mod, "run_training", _fake_run_training)

    stages = build_stages(
        charter=charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        runs_db_path=runs_db,
        otel_feed_dir=tmp_path / "feed",
        datasets_dir=tmp_path / "datasets",
        registry_dir=tmp_path / "registry",
        usage_db=tmp_path / "usage.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
        checkpoints_dir=tmp_path / "checkpoints",
        suites_dir=tmp_path / "suites",
    )
    # the train stage's now_fn is left at default (real clock); its budget gate compares hours, not
    # timestamps, so it stays deterministic. only evaluate needs a fixed clock for its eval block.
    evaluate = stages["evaluate"]
    assert isinstance(evaluate, EvaluateStage)
    # build_stages wires the default anchor judge + probe; replace them with deterministic fakes so the
    # candidate scores above 0.5 and reads drift-clean without touching a provider.
    evaluate.judge_factory = lambda anchor: _FixedScorer(0.9)
    evaluate.probe_fn = _clean_probe
    evaluate.now_fn = lambda: _NOW

    ctx = _ctx(tmp_path, charter)

    # drive the full loop in cycle order (advise is a no-op without a proposal store, but runs to prove
    # the ordered set clears end to end).
    results = {name: stages[name].run_once(ctx) for name in STAGE_NAMES}

    # each stage did its unit of work with no errors.
    assert all(result.errors == 0 for result in results.values())
    assert results["ingest"].processed > 0  # native generations + agent outputs landed in the trace store
    assert results["curate"].processed == 3  # three completed competitor traces became training records
    assert results["train"].processed == 1  # one candidate trained + published
    assert results["evaluate"].processed == 1  # candidate scored under the anchor
    assert results["promote"].processed == 1  # candidate activated (no incumbent)

    # the shared registry now holds exactly one record for the target, and it is ACTIVE.
    registry = ModelRegistry(tmp_path / "registry")
    target_records = [r for r in registry.list_all() if r.metadata.get("target") == _TARGET]
    assert len(target_records) == 1
    promoted = target_records[0]
    assert promoted.activation_state == "active"
    assert promoted.metadata["eval"]["score"] == 0.9
    assert promoted.metadata["eval"]["drift_ok"] is True

    # serving resolution for the target routes to that promoted artifact from the registry, not the fallback.
    decision = resolve_active_serving(registry, _TARGET, _BACKEND)
    assert decision.source == "registry"
    assert decision.fallback_used is False
    assert decision.artifact_id == promoted.artifact_id
    assert decision.model == promoted.checkpoint_path

    # the promote stage announced the activation for this target.
    activated = [e for e in _events(tmp_path) if e["event"] == "promote_activated"]
    assert activated and activated[0]["payload"]["target"] == _TARGET
    assert activated[0]["payload"]["artifact_id"] == promoted.artifact_id
