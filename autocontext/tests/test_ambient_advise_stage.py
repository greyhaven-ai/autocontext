"""tests for the advise stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.ambient.advise import AdviseStage
from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.proposals import ProposalStore, apply_proposal
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.stage import StageContext
from autocontext.ambient.trace_store import TraceStore
from autocontext.harness.core.events import EventStreamEmitter


def _target(name: str = "competitor-local", **overrides: Any) -> CharterTarget:
    base: dict[str, Any] = {
        "name": name,
        "kind": "role",
        "selector": "competitor",
        "base_model": "qwen2.5-3b",
        "min_dataset_records": 10,
        "eval_suite": "anchor-v1",
    }
    base.update(overrides)
    return CharterTarget(**base)


def _charter(targets: list[CharterTarget]) -> Charter:
    return Charter(
        tier="oss",
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=targets,
        budgets=CharterBudgets(gpu_hours_per_window=1.0, window_hours=24, disk_quota_gb=1.0),
    )


def _ctx(tmp_path: Path, charter: Charter, store: ProposalStore | None) -> StageContext:
    return StageContext(
        charter=charter,
        queue=AmbientQueue(tmp_path / "queue.sqlite3"),
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        proposal_store=store,
    )


def _seed_scenario(traces: TraceStore, scenario: str, count: int, score: float) -> None:
    for index in range(count):
        traces.append(
            "autocontext-outputs:native",
            "agent_output",
            {
                "run_id": f"run_{scenario}",
                "scenario": scenario,
                "generation_index": index,
                "role": "competitor",
                "content": f"strategy {index}",
                "status": "completed",
                "mean_score": score,
                "best_score": score,
                "gate_decision": "advance",
                "created_at": "t",
            },
            "frontier",
            0,
        )


def test_uncovered_high_quality_scenario_yields_a_proposal(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    # template target is evaluative-free but role-kind targets count as
    # coverage, so template from a task_family target here
    template = _target(name="othello-target", kind="task_family", selector="othello")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    result = stage.run_once(_ctx(tmp_path, _charter([template]), store))

    assert result.processed == 1
    pending = store.pending()
    assert len(pending) == 1
    proposal = pending[0]
    assert proposal.kind == "add_target"
    assert proposal.payload["selector"] == "lean_prover"
    assert proposal.payload["kind"] == "task_family"
    assert proposal.payload["base_model"] == "qwen2.5-3b"
    assert "lean_prover" in proposal.rationale
    # the emitted payload must survive the plan-1 apply path end to end
    updated = apply_proposal(_charter([template]), proposal)
    assert any(t.selector == "lean_prover" for t in updated.targets)


def test_below_volume_floor_yields_nothing(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=3, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    result = stage.run_once(_ctx(tmp_path, _charter([_target(kind="task_family", selector="othello")]), store))

    assert result.processed == 0
    assert store.pending() == []


def test_low_mean_score_yields_nothing(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.2)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    result = stage.run_once(_ctx(tmp_path, _charter([_target(kind="task_family", selector="othello")]), store))

    assert result.processed == 0


def test_task_family_target_covers_its_scenario(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    covered = _charter([_target(name="prover", kind="task_family", selector="lean_prover")])
    assert stage.run_once(_ctx(tmp_path, covered, store)).processed == 0


def test_generator_role_target_covers_all_scenarios(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    assert stage.run_once(_ctx(tmp_path, _charter([_target()]), store)).processed == 0


def test_non_competitor_role_target_does_not_cover_scenarios(tmp_path: Path) -> None:
    # the advisor's signal is competitor-only, so a lone analyst role target
    # must not permanently suppress an otherwise-qualifying scenario proposal
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    charter = _charter([_target(name="analyst-local", selector="analyst")])
    result = stage.run_once(_ctx(tmp_path, charter, store))

    assert result.processed == 1
    assert store.pending()[0].payload["selector"] == "lean_prover"


def test_scoped_competitor_role_covers_only_its_scenario(tmp_path: Path) -> None:
    # a competitor role bound to one scenario (competitor@othello) covers only
    # othello: it must not suppress an otherwise-qualifying lean_prover
    # proposal, and othello itself stays covered
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    _seed_scenario(traces, "othello", count=5, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    charter = _charter([_target(name="competitor-othello", selector="competitor@othello")])
    result = stage.run_once(_ctx(tmp_path, charter, store))

    assert result.processed == 1
    pending = store.pending()
    assert len(pending) == 1
    assert pending[0].payload["selector"] == "lean_prover"


def test_pending_proposal_is_not_duplicated(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    charter = _charter([_target(name="othello-target", kind="task_family", selector="othello")])
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    first = stage.run_once(_ctx(tmp_path, charter, store))
    second = stage.run_once(_ctx(tmp_path, charter, store))

    assert first.processed == 1
    assert second.processed == 0
    assert len(store.pending()) == 1


def test_no_template_target_emits_event_not_proposal(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    result = stage.run_once(_ctx(tmp_path, _charter([]), store))

    assert result.processed == 0
    assert store.pending() == []
    events = [json.loads(line) for line in (tmp_path / "events.ndjson").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e["event"] == "advise_no_template" for e in events)


def test_pattern_invalid_scenario_is_skipped_not_raised(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    # "_probe" leads with an underscore, which violates the CharterTarget.name
    # slug pattern; it must be skipped without tripping the stage breaker while
    # a valid sibling scenario still yields its proposal
    _seed_scenario(traces, "_probe", count=5, score=0.9)
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    store = ProposalStore(tmp_path / "proposals.jsonl")
    template = _target(name="othello-target", kind="task_family", selector="othello")
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    result = stage.run_once(_ctx(tmp_path, _charter([template]), store))

    assert result.processed == 1
    pending = store.pending()
    assert len(pending) == 1
    assert pending[0].payload["selector"] == "lean_prover"
    events = [json.loads(line) for line in (tmp_path / "events.ndjson").read_text(encoding="utf-8").splitlines() if line.strip()]
    invalid = [e for e in events if e["event"] == "advise_invalid_scenario"]
    assert len(invalid) == 1
    assert invalid[0]["payload"]["scenario"] == "_probe"


def test_missing_proposal_store_is_a_quiet_skip(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    _seed_scenario(traces, "lean_prover", count=5, score=0.9)
    stage = AdviseStage(name="advise", trace_store=traces, min_traces=5)

    result = stage.run_once(_ctx(tmp_path, _charter([]), None))

    assert result.processed == 0
    assert result.errors == 0
