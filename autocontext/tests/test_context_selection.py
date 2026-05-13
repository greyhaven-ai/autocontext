from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from autocontext.config.settings import AppSettings
from autocontext.extensions import HookBus, HookEvents, HookResult
from autocontext.loop.stage_helpers.semantic_benchmark import prepare_generation_prompts
from autocontext.loop.stage_types import GenerationContext
from autocontext.scenarios.base import Observation, ScenarioInterface
from autocontext.storage.artifacts import ArtifactStore
from autocontext.util.json_io import read_json


def _artifact_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        tmp_path / "runs",
        tmp_path / "knowledge",
        tmp_path / "skills",
        tmp_path / ".claude" / "skills",
    )


def _generation_context(tmp_path: Path, *, hook_bus: HookBus | None = None) -> tuple[ArtifactStore, GenerationContext]:
    artifacts = _artifact_store(tmp_path)
    settings = AppSettings(
        runs_root=artifacts.runs_root,
        knowledge_root=artifacts.knowledge_root,
        skills_root=artifacts.skills_root,
        claude_skills_path=artifacts.claude_skills_path,
    )
    return artifacts, GenerationContext(
        run_id="run-1",
        scenario_name="grid_ctf",
        scenario=cast(ScenarioInterface, object()),
        generation=2,
        settings=settings,
        previous_best=0.4,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="hint text",
        replay_narrative="",
        hook_bus=hook_bus,
    )


def _prepare_generation_prompts(ctx: GenerationContext, artifacts: ArtifactStore, *, current_playbook: str = "abcd") -> None:
    prepare_generation_prompts(
        ctx,
        artifacts=artifacts,
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=Observation(narrative="obs", state={}, constraints=[]),
        current_playbook=current_playbook,
        available_tools="efgh",
        operational_lessons="abcd",
        replay_narrative="",
        coach_competitor_hints="hint text",
        coach_hint_feedback="",
        recent_analysis="",
        analyst_feedback="",
        analyst_attribution="",
        coach_attribution="",
        architect_attribution="",
        score_trajectory="",
        strategy_registry="",
        progress_json="",
        experiment_log="",
        dead_ends="",
        research_protocol="",
        session_reports="",
        architect_tool_usage_report="",
        constraint_mode=False,
        context_budget_tokens=0,
        notebook_contexts=None,
        environment_snapshot="",
        evidence_manifest="",
        evidence_manifests=None,
        evidence_cache_hits=0,
        evidence_cache_lookups=0,
    )


def _context_selection_payload(artifacts: ArtifactStore) -> dict[str, Any]:
    return read_json(artifacts.runs_root / "run-1" / "context_selection" / "gen_2_generation_prompt_context.json")


def test_context_selection_decision_calculates_selection_quality_metrics() -> None:
    from autocontext.knowledge.context_selection import (
        ContextSelectionCandidate,
        ContextSelectionDecision,
    )

    duplicate_content = "abcdefgh"
    decision = ContextSelectionDecision(
        run_id="run-1",
        scenario_name="grid_ctf",
        generation=3,
        stage="prompt_context",
        created_at="2026-01-02T03:04:05+00:00",
        candidates=(
            ContextSelectionCandidate.from_contents(
                artifact_id="playbook",
                artifact_type="prompt_component",
                source="knowledge",
                candidate_content=duplicate_content,
                selected_content=duplicate_content,
                selection_reason="retained",
                useful=True,
                freshness_generation_delta=1,
            ),
            ContextSelectionCandidate.from_contents(
                artifact_id="lessons",
                artifact_type="prompt_component",
                source="knowledge",
                candidate_content=duplicate_content,
                selected_content=duplicate_content,
                selection_reason="retained",
                freshness_generation_delta=2,
            ),
            ContextSelectionCandidate.from_contents(
                artifact_id="analysis",
                artifact_type="prompt_component",
                source="knowledge",
                candidate_content="ijklmnop",
                selected_content="",
                selection_reason="empty after budget",
                useful=True,
            ),
            ContextSelectionCandidate.from_contents(
                artifact_id="tools",
                artifact_type="prompt_component",
                source="knowledge",
                candidate_content="qrst",
                selected_content="qrst",
                selection_reason="retained",
            ),
        ),
    )

    metrics = decision.metrics()

    assert metrics["candidate_count"] == 4
    assert metrics["selected_count"] == 3
    assert metrics["candidate_token_estimate"] == 7
    assert metrics["selected_token_estimate"] == 5
    assert metrics["selection_rate"] == pytest.approx(0.75)
    assert metrics["duplicate_content_rate"] == pytest.approx(1 / 3)
    assert metrics["useful_candidate_count"] == 2
    assert metrics["useful_selected_count"] == 1
    assert metrics["useful_artifact_recall"] == pytest.approx(0.5)
    assert metrics["mean_selected_freshness_generation_delta"] == pytest.approx(1.5)


def test_context_selection_decision_round_trips_without_prompt_content() -> None:
    from autocontext.knowledge.context_selection import (
        ContextSelectionCandidate,
        ContextSelectionDecision,
    )

    decision = ContextSelectionDecision(
        run_id="run-1",
        scenario_name="grid_ctf",
        generation=2,
        stage="prompt_context",
        created_at="2026-01-02T03:04:05+00:00",
        candidates=(
            ContextSelectionCandidate.from_contents(
                artifact_id="playbook",
                artifact_type="prompt_component",
                source="knowledge",
                candidate_content="secret strategy text",
                selected_content="secret",
                selection_reason="trimmed",
            ),
        ),
        metadata={"context_budget_tokens": 1200},
    )

    payload = decision.to_dict()
    restored = ContextSelectionDecision.from_dict(payload)

    assert restored == decision
    assert "secret strategy text" not in str(payload)
    assert payload["metrics"]["selected_token_estimate"] == 1


def test_persist_context_selection_decision_writes_under_run_root(tmp_path: Path) -> None:
    from autocontext.knowledge.context_selection import (
        ContextSelectionCandidate,
        ContextSelectionDecision,
    )
    from autocontext.storage.context_selection_store import persist_context_selection_decision

    artifacts = _artifact_store(tmp_path)
    decision = ContextSelectionDecision(
        run_id="run-1",
        scenario_name="grid_ctf",
        generation=4,
        stage="prompt_context",
        created_at="2026-01-02T03:04:05+00:00",
        candidates=(
            ContextSelectionCandidate.from_contents(
                artifact_id="playbook",
                artifact_type="prompt_component",
                source="knowledge",
                candidate_content="abcd",
                selected_content="abcd",
                selection_reason="retained",
            ),
        ),
    )

    path = persist_context_selection_decision(artifacts, decision)

    assert path == artifacts.runs_root / "run-1" / "context_selection" / "gen_4_prompt_context.json"
    assert read_json(path)["metrics"]["selected_count"] == 1


def test_persist_context_selection_decision_rejects_unsafe_names(tmp_path: Path) -> None:
    from autocontext.knowledge.context_selection import ContextSelectionDecision
    from autocontext.storage.context_selection_store import persist_context_selection_decision

    artifacts = _artifact_store(tmp_path)
    unsafe_run = ContextSelectionDecision(
        run_id="../outside",
        scenario_name="grid_ctf",
        generation=1,
        stage="prompt_context",
        candidates=(),
    )
    unsafe_stage = ContextSelectionDecision(
        run_id="run-1",
        scenario_name="grid_ctf",
        generation=1,
        stage="../prompt_context",
        candidates=(),
    )

    with pytest.raises(ValueError):
        persist_context_selection_decision(artifacts, unsafe_run)
    with pytest.raises(ValueError):
        persist_context_selection_decision(artifacts, unsafe_stage)
    assert not (tmp_path / "outside").exists()


def test_context_selection_report_aggregates_decision_metrics() -> None:
    from autocontext.knowledge.context_selection import (
        ContextSelectionCandidate,
        ContextSelectionDecision,
    )
    from autocontext.knowledge.context_selection_report import build_context_selection_report

    report = build_context_selection_report(
        (
            ContextSelectionDecision(
                run_id="run-1",
                scenario_name="grid_ctf",
                generation=1,
                stage="generation_prompt_context",
                created_at="2026-01-02T03:04:05+00:00",
                candidates=(
                    ContextSelectionCandidate.from_contents(
                        artifact_id="playbook",
                        artifact_type="prompt_component",
                        source="prompt_assembly",
                        candidate_content="a" * 40,
                        selected_content="a" * 40,
                        selection_reason="retained",
                        useful=True,
                        freshness_generation_delta=1,
                    ),
                    ContextSelectionCandidate.from_contents(
                        artifact_id="analysis",
                        artifact_type="prompt_component",
                        source="prompt_assembly",
                        candidate_content="b" * 20,
                        selected_content="",
                        selection_reason="removed",
                        useful=True,
                    ),
                ),
            ),
            ContextSelectionDecision(
                run_id="run-1",
                scenario_name="grid_ctf",
                generation=2,
                stage="generation_prompt_context",
                created_at="2026-01-02T03:05:05+00:00",
                candidates=(
                    ContextSelectionCandidate.from_contents(
                        artifact_id="playbook",
                        artifact_type="prompt_component",
                        source="prompt_assembly",
                        candidate_content="c" * 20,
                        selected_content="c" * 20,
                        selection_reason="retained",
                        freshness_generation_delta=2,
                    ),
                    ContextSelectionCandidate.from_contents(
                        artifact_id="lessons",
                        artifact_type="prompt_component",
                        source="prompt_assembly",
                        candidate_content="c" * 20,
                        selected_content="c" * 20,
                        selection_reason="retained",
                        freshness_generation_delta=4,
                    ),
                ),
            ),
        )
    )

    payload = report.to_dict()

    assert payload["status"] == "completed"
    assert payload["run_id"] == "run-1"
    assert payload["scenario_name"] == "grid_ctf"
    assert payload["decision_count"] == 2
    assert payload["generation_count"] == 2
    assert payload["summary"]["selected_token_estimate"] == 20
    assert payload["summary"]["candidate_token_estimate"] == 25
    assert payload["summary"]["selection_rate"] == pytest.approx(0.75)
    assert payload["summary"]["mean_duplicate_content_rate"] == pytest.approx(0.25)
    assert payload["summary"]["mean_selected_token_estimate"] == pytest.approx(10)
    assert payload["summary"]["max_selected_token_estimate"] == 10
    assert payload["summary"]["mean_useful_artifact_recall"] == pytest.approx(0.5)
    assert payload["summary"]["mean_selected_freshness_generation_delta"] == pytest.approx(2)
    assert [stage["generation"] for stage in payload["stages"]] == [1, 2]


def test_context_selection_store_loads_decisions_in_generation_order(tmp_path: Path) -> None:
    from autocontext.knowledge.context_selection import (
        ContextSelectionCandidate,
        ContextSelectionDecision,
    )
    from autocontext.storage.context_selection_store import (
        load_context_selection_decisions,
        persist_context_selection_decision,
    )

    artifacts = _artifact_store(tmp_path)
    gen2 = ContextSelectionDecision(
        run_id="run-1",
        scenario_name="grid_ctf",
        generation=2,
        stage="generation_prompt_context",
        candidates=(),
    )
    gen1 = ContextSelectionDecision(
        run_id="run-1",
        scenario_name="grid_ctf",
        generation=1,
        stage="generation_prompt_context",
        candidates=(
            ContextSelectionCandidate.from_contents(
                artifact_id="playbook",
                artifact_type="prompt_component",
                source="prompt_assembly",
                candidate_content="abcd",
                selected_content="abcd",
                selection_reason="retained",
            ),
        ),
    )
    persist_context_selection_decision(artifacts, gen2)
    persist_context_selection_decision(artifacts, gen1)

    decisions = load_context_selection_decisions(artifacts.runs_root, "run-1")

    assert [decision.generation for decision in decisions] == [1, 2]


def test_context_selection_store_ignores_non_decision_json(tmp_path: Path) -> None:
    from autocontext.knowledge.context_selection import (
        ContextSelectionCandidate,
        ContextSelectionDecision,
    )
    from autocontext.knowledge.context_selection_report import build_context_selection_report
    from autocontext.storage.context_selection_store import (
        load_context_selection_decisions,
        persist_context_selection_decision,
    )

    artifacts = _artifact_store(tmp_path)
    persist_context_selection_decision(
        artifacts,
        ContextSelectionDecision(
            run_id="run-1",
            scenario_name="grid_ctf",
            generation=1,
            stage="generation_prompt_context",
            candidates=(
                ContextSelectionCandidate.from_contents(
                    artifact_id="playbook",
                    artifact_type="prompt_component",
                    source="prompt_assembly",
                    candidate_content="abcd",
                    selected_content="abcd",
                    selection_reason="retained",
                ),
            ),
        ),
    )
    context_dir = artifacts.runs_root / "run-1" / "context_selection"
    artifacts.write_json(context_dir / "summary.json", {"status": "completed"})
    artifacts.write_json(
        context_dir / "gen_2_summary.json",
        {
            "schema_version": 1,
            "run_id": "other-run",
            "scenario_name": "grid_ctf",
            "generation": 2,
            "stage": "summary",
            "candidates": [],
        },
    )
    artifacts.write_json(
        context_dir / "gen_3_missing_schema.json",
        {
            "run_id": "run-1",
            "scenario_name": "grid_ctf",
            "generation": 3,
            "stage": "missing_schema",
            "candidates": [],
        },
    )
    artifacts.write_json(
        context_dir / "gen_4_metadata.json",
        {
            "schema_version": 1,
            "run_id": "run-1",
            "scenario_name": "grid_ctf",
            "generation": 4,
            "stage": "metadata",
            "candidates": [],
        },
    )

    decisions = load_context_selection_decisions(artifacts.runs_root, "run-1")
    payload = build_context_selection_report(decisions).to_dict()

    assert [decision.generation for decision in decisions] == [1]
    assert payload["decision_count"] == 1
    assert payload["summary"]["mean_selected_token_estimate"] == pytest.approx(1)


def test_context_selection_store_rejects_run_id_escape(tmp_path: Path) -> None:
    from autocontext.storage.context_selection_store import load_context_selection_decisions

    with pytest.raises(ValueError):
        load_context_selection_decisions(tmp_path / "runs", "../outside")
    assert not (tmp_path / "outside").exists()


def test_prepare_generation_prompts_persists_context_selection_artifact(tmp_path: Path) -> None:
    artifacts, ctx = _generation_context(tmp_path)

    _prepare_generation_prompts(ctx, artifacts)

    payload = _context_selection_payload(artifacts)

    assert payload["run_id"] == "run-1"
    assert payload["stage"] == "generation_prompt_context"
    assert payload["metrics"]["selected_count"] >= 3
    assert payload["metrics"]["selected_token_estimate"] > 0
    assert payload["metrics"]["duplicate_content_rate"] > 0


def test_context_selection_candidates_reflect_context_components_hook(tmp_path: Path) -> None:
    bus = HookBus()
    expanded_playbook = "x" * 400

    def expand_playbook(event: Any) -> HookResult:
        components = dict(event.payload["components"])
        components["current_playbook"] = expanded_playbook
        return HookResult(payload={"components": components})

    bus.on(HookEvents.CONTEXT_COMPONENTS, expand_playbook)
    artifacts, ctx = _generation_context(tmp_path, hook_bus=bus)

    _prepare_generation_prompts(ctx, artifacts, current_playbook="abcd")

    payload = _context_selection_payload(artifacts)
    playbook = next(candidate for candidate in payload["candidates"] if candidate["artifact_id"] == "playbook")
    assert playbook["candidate_token_estimate"] == 100


def test_context_selection_selected_components_reflect_final_context_hook(tmp_path: Path) -> None:
    bus = HookBus()

    def redact_roles(event: Any) -> HookResult:
        return HookResult(payload={"roles": {role: "redacted" for role in event.payload["roles"]}})

    bus.on(HookEvents.CONTEXT, redact_roles)
    artifacts, ctx = _generation_context(tmp_path, hook_bus=bus)

    _prepare_generation_prompts(ctx, artifacts)

    payload = _context_selection_payload(artifacts)
    assert payload["metrics"]["candidate_count"] > 0
    assert payload["metrics"]["selected_count"] == 0
