"""Tests for AC-210: Operator cockpit — read-only review over existing artifacts."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.config.settings import AppSettings
from autocontext.server.changelog import build_changelog
from autocontext.server.cockpit_api import cockpit_router
from autocontext.server.writeup import generate_writeup, generate_writeup_html
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)
    return store


def _make_artifacts(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


def _seed_run(store: SQLiteStore, run_id: str = "run1", scenario: str = "grid_ctf", gens: int = 3) -> None:
    """Create a run with completed generations for testing."""
    store.create_run(run_id, scenario, gens, "local")
    store.upsert_generation(run_id, 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed", 30.0)
    store.upsert_generation(run_id, 2, 0.55, 0.65, 1050.0, 3, 0, "advance", "completed", 45.0)
    store.upsert_generation(run_id, 3, 0.70, 0.80, 1100.0, 4, 1, "advance", "completed", 60.0)
    store.mark_run_completed(run_id)


def _seed_agent_outputs(store: SQLiteStore, run_id: str = "run1") -> None:
    """Add agent outputs including architect and competitor."""
    store.append_agent_output(run_id, 1, "competitor", '{"aggression": 0.5}')
    store.append_agent_output(run_id, 1, "analyst", "Gen 1 analysis: baseline strategy.")
    store.append_agent_output(run_id, 2, "competitor", '{"aggression": 0.7}')
    store.append_agent_output(run_id, 2, "architect", '[{"name": "tool_a", "code": "pass"}]')
    store.append_agent_output(run_id, 3, "competitor", '{"aggression": 0.9}')


@pytest.fixture()
def cockpit_env(tmp_path: Path) -> Generator[dict[str, Any], None, None]:
    """Build an app with explicit state-backed store/settings for cockpit API."""

    store = _make_store(tmp_path)
    artifacts = _make_artifacts(tmp_path)
    settings = AppSettings(
        db_path=tmp_path / "test.db",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )

    app = FastAPI()
    app.state.store = store
    app.state.app_settings = settings
    app.include_router(cockpit_router)
    client = TestClient(app)

    yield {"store": store, "artifacts": artifacts, "client": client, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# Writeup generation
# ---------------------------------------------------------------------------


class TestWriteup:
    def test_generates_markdown(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = generate_writeup("run1", store, artifacts)
        assert isinstance(result, str)
        assert "run1" in result
        assert "# Run Summary" in result

    def test_includes_score_trajectory(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = generate_writeup("run1", store, artifacts)
        assert "Score Trajectory" in result
        assert "0.50" in result or "0.5" in result

    def test_includes_gate_decisions(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = generate_writeup("run1", store, artifacts)
        assert "advance" in result

    def test_includes_playbook_excerpt(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        playbook_dir = tmp_path / "knowledge" / "grid_ctf"
        playbook_dir.mkdir(parents=True)
        (playbook_dir / "playbook.md").write_text("# Evolved Playbook\n\nUse flanking.", encoding="utf-8")
        result = generate_writeup("run1", store, artifacts)
        assert "Playbook" in result
        assert "flanking" in result

    def test_empty_run(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        store.create_run("empty", "grid_ctf", 3, "local")
        result = generate_writeup("empty", store, artifacts)
        assert "empty" in result

    def test_includes_best_strategy(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        _seed_agent_outputs(store)
        result = generate_writeup("run1", store, artifacts)
        assert "Best Strategy" in result or "Strategy" in result

    def test_prefers_persisted_trace_grounded_writeup(self, tmp_path: Path) -> None:
        from autocontext.analytics.trace_reporter import ReportStore, TraceWriteup

        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)

        report_store = ReportStore(tmp_path / "knowledge" / "analytics")
        report_store.persist_writeup(TraceWriteup(
            writeup_id="trace-writeup-1",
            run_id="run1",
            generation_index=None,
            findings=[],
            failure_motifs=[],
            recovery_paths=[],
            summary="Trace-grounded summary from canonical events.",
            created_at="2026-03-15T12:00:00Z",
            metadata={"scenario": "grid_ctf", "scenario_family": "simulation"},
        ))

        result = generate_writeup("run1", store, artifacts)
        assert "Trace-grounded summary from canonical events." in result
        assert "# Run Summary: run1" in result

    def test_generates_html_from_persisted_trace_grounded_writeup(self, tmp_path: Path) -> None:
        from autocontext.analytics.trace_reporter import ReportStore, TraceFinding, TraceWriteup

        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)

        report_store = ReportStore(tmp_path / "knowledge" / "analytics")
        report_store.persist_writeup(TraceWriteup(
            writeup_id="trace-writeup-1",
            run_id="run1",
            generation_index=None,
            findings=[
                TraceFinding(
                    finding_id="finding-1",
                    finding_type="weakness",
                    title="Escaped <finding>",
                    description="Needs review.",
                    evidence_event_ids=["event-1"],
                    severity="medium",
                    category="failure_motif",
                ),
            ],
            failure_motifs=[],
            recovery_paths=[],
            summary="Trace-grounded <summary>.",
            created_at="2026-03-15T12:00:00Z",
            metadata={"scenario": "grid_ctf"},
        ))

        html = generate_writeup_html("run1", store, artifacts)

        assert "Run Summary: run1" in html
        assert "Trace-grounded &lt;summary&gt;." in html
        assert "Escaped &lt;finding&gt;" in html

    def test_cockpit_writeup_endpoint_returns_html_additively(self, cockpit_env: dict[str, Any]) -> None:
        client: TestClient = cockpit_env["client"]
        store: SQLiteStore = cockpit_env["store"]
        _seed_run(store)

        response = client.get("/api/cockpit/writeup/run1")

        assert response.status_code == 200
        payload = response.json()
        assert "writeup_markdown" in payload
        assert "writeup_html" in payload
        assert payload["writeup_html_path"].endswith("knowledge/grid_ctf/reports/run1.html")
        assert "Run Summary: run1" in payload["writeup_html"]
        assert Path(payload["writeup_html_path"]).read_text(encoding="utf-8") == payload["writeup_html"]

    def test_scenario_curation_endpoint_persists_read_only_html(self, cockpit_env: dict[str, Any]) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        client: TestClient = cockpit_env["client"]
        artifacts: ArtifactStore = cockpit_env["artifacts"]

        artifacts.lesson_store.add_lesson(
            "grid_ctf",
            "Always verify posted charges before refunding.",
            ApplicabilityMeta(created_at="2026-05-11T12:00:00Z", generation=3, best_score=0.72),
        )
        artifacts.write_hints("grid_ctf", "Prefer concise escalation.")
        artifacts.append_dead_end("grid_ctf", "Do not retry invalid account states.")

        response = client.get("/api/cockpit/scenarios/grid_ctf/curation")

        assert response.status_code == 200
        payload = response.json()
        assert payload["scenario_name"] == "grid_ctf"
        assert payload["curation_html_path"].endswith("knowledge/grid_ctf/curation.html")
        assert "Read-only derived artifact" in payload["curation_html"]
        assert "Always verify posted charges before refunding." in payload["curation_html"]
        assert Path(payload["curation_html_path"]).read_text(encoding="utf-8") == payload["curation_html"]

    def test_scenario_curation_endpoint_rejects_dot_segments(self, cockpit_env: dict[str, Any]) -> None:
        client: TestClient = cockpit_env["client"]
        tmp_path: Path = cockpit_env["tmp_path"]

        response = client.get("/api/cockpit/scenarios/%2E%2E/curation")

        assert response.status_code == 422
        assert not (tmp_path / "curation.html").exists()

    @pytest.mark.parametrize("scenario_name", [".", "..", "nested/name", r"nested\name"])
    def test_scenario_curation_writer_rejects_path_escape(self, tmp_path: Path, scenario_name: str) -> None:
        artifacts = _make_artifacts(tmp_path)

        with pytest.raises(ValueError, match="single path segment"):
            artifacts.write_scenario_curation_html(scenario_name, "<html></html>")

        assert not (tmp_path / "curation.html").exists()


# ---------------------------------------------------------------------------
# Changelog builder
# ---------------------------------------------------------------------------


class TestChangelog:
    def test_builds_changelog(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        assert result["run_id"] == "run1"
        assert isinstance(result["generations"], list)
        assert len(result["generations"]) >= 2

    def test_score_deltas(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        gens = result["generations"]
        gen2 = next(g for g in gens if g["generation"] == 2)
        assert abs(gen2["score_delta"] - 0.15) < 0.01

    def test_elo_deltas(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        gens = result["generations"]
        gen2 = next(g for g in gens if g["generation"] == 2)
        assert abs(gen2["elo_delta"] - 50.0) < 0.01

    def test_gate_decision_included(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        for gen in result["generations"]:
            assert "gate_decision" in gen

    def test_empty_run(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        store.create_run("empty", "grid_ctf", 3, "local")
        result = build_changelog("empty", store, artifacts)
        assert result["run_id"] == "empty"
        assert result["generations"] == []

    def test_new_tools_detected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        _seed_agent_outputs(store)
        result = build_changelog("run1", store, artifacts)
        gen2 = next(g for g in result["generations"] if g["generation"] == 2)
        assert "new_tools" in gen2

    def test_duration_included(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        for gen in result["generations"]:
            assert "duration_seconds" in gen

    def test_playbook_changed_tracks_real_coach_outputs(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        store.append_agent_output(
            "run1",
            2,
            "coach",
            "<!-- PLAYBOOK_START -->Use flanking.<!-- PLAYBOOK_END -->",
        )

        result = build_changelog("run1", store, artifacts)
        gen1 = next(g for g in result["generations"] if g["generation"] == 1)
        gen2 = next(g for g in result["generations"] if g["generation"] == 2)

        assert gen1["playbook_changed"] is False
        assert gen2["playbook_changed"] is True


# ---------------------------------------------------------------------------
# Cockpit API endpoints
# ---------------------------------------------------------------------------


class TestCockpitRunsEndpoint:
    def test_list_runs(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        run = data[0]
        assert run["run_id"] == "run1"
        assert run["scenario_name"] == "grid_ctf"
        assert run["generations_completed"] == 3
        assert run["best_score"] == 0.80
        assert run["status"] == "completed"

    def test_list_runs_empty(self, cockpit_env: dict[str, Any]) -> None:
        resp = cockpit_env["client"].get("/api/cockpit/runs")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCockpitRunStatus:
    def test_run_status(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert data["scenario_name"] == "grid_ctf"
        assert isinstance(data["generations"], list)
        assert len(data["generations"]) == 3
        gen1 = data["generations"][0]
        assert gen1["generation"] == 1
        assert gen1["mean_score"] == 0.40
        assert gen1["best_score"] == 0.50
        assert gen1["elo"] == 1000.0
        assert gen1["gate_decision"] == "advance"

    def test_run_context_selection_report(self, cockpit_env: dict[str, Any]) -> None:
        from autocontext.knowledge.context_selection import (
            ContextSelectionCandidate,
            ContextSelectionDecision,
        )
        from autocontext.storage.context_selection_store import persist_context_selection_decision

        client: TestClient = cockpit_env["client"]
        artifacts: ArtifactStore = cockpit_env["artifacts"]
        persist_context_selection_decision(
            artifacts,
            ContextSelectionDecision(
                run_id="run1",
                scenario_name="grid_ctf",
                generation=1,
                stage="generation_prompt_context",
                candidates=(
                    ContextSelectionCandidate.from_contents(
                        artifact_id="playbook",
                        artifact_type="prompt_component",
                        source="prompt_assembly",
                        candidate_content="x" * 400,
                        selected_content="x" * 80,
                        selection_reason="trimmed",
                    ),
                ),
                metadata={
                    "context_budget_telemetry": {
                        "input_token_estimate": 120,
                        "output_token_estimate": 20,
                        "trimmed_component_count": 1,
                    },
                    "prompt_compaction_cache": {"hits": 0, "misses": 10, "lookups": 10},
                },
            ),
        )

        resp = client.get("/api/cockpit/runs/run1/context-selection")

        assert resp.status_code == 200
        payload = resp.json()
        cards = {card["key"]: card for card in payload["telemetry_cards"]}
        assert payload["run_id"] == "run1"
        assert payload["summary"]["budget_token_reduction"] == 100
        assert cards["context_budget"]["severity"] == "warning"
        assert cards["semantic_compaction_cache"]["value"] == "0.0% hit rate"

    def test_run_context_selection_report_missing(self, cockpit_env: dict[str, Any]) -> None:
        resp = cockpit_env["client"].get("/api/cockpit/runs/missing/context-selection")

        assert resp.status_code == 404
        assert "No context selection artifacts" in resp.json()["detail"]


class TestCockpitChangelog:
    def test_changelog_endpoint(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/changelog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert isinstance(data["generations"], list)


class TestCockpitCompare:
    def test_compare_generations(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/compare/1/3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gen_a"]["generation"] == 1
        assert data["gen_b"]["generation"] == 3
        assert abs(data["score_delta"] - 0.30) < 0.01
        assert abs(data["elo_delta"] - 100.0) < 0.01

    def test_compare_nonexistent_generation(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/compare/1/99")
        assert resp.status_code == 404


class TestCockpitResume:
    def test_resume_completed_run(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert data["status"] == "completed"
        assert data["last_generation"] == 3
        assert data["can_resume"] is False

    def test_resume_running_run(self, cockpit_env: dict[str, Any]) -> None:
        store = cockpit_env["store"]
        store.create_run("running1", "grid_ctf", 5, "local")
        store.upsert_generation("running1", 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed", 30.0)
        store.upsert_generation("running1", 2, 0.55, 0.65, 1050.0, 3, 0, "advance", "completed", 45.0)
        resp = cockpit_env["client"].get("/api/cockpit/runs/running1/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "running1"
        assert data["status"] == "running"
        assert data["last_generation"] == 2
        assert data["can_resume"] is True

    def test_resume_nonexistent_run(self, cockpit_env: dict[str, Any]) -> None:
        resp = cockpit_env["client"].get("/api/cockpit/runs/nonexistent/resume")
        assert resp.status_code == 404


class TestCockpitWriteup:
    def test_writeup_endpoint(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/writeup/run1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert data["scenario_name"] == "grid_ctf"
        assert isinstance(data["writeup_markdown"], str)
        assert "# Run Summary" in data["writeup_markdown"]

    def test_writeup_nonexistent_run(self, cockpit_env: dict[str, Any]) -> None:
        resp = cockpit_env["client"].get("/api/cockpit/writeup/nonexistent")
        assert resp.status_code == 404
