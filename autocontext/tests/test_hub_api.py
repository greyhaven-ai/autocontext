"""Integration tests for the research hub API (AC-267)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from autocontext.analytics.facets import DelightSignal, FrictionSignal, RunFacet
from autocontext.analytics.store import FacetStore
from autocontext.knowledge.normalized_metrics import (
    CostEfficiency,
    NormalizedProgress,
    RunProgressReport,
)
from autocontext.knowledge.weakness import Weakness, WeaknessReport
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore


def _make_facet() -> RunFacet:
    return RunFacet(
        run_id="run-42",
        scenario="grid_ctf",
        scenario_family="game",
        agent_provider="anthropic",
        executor_mode="local",
        total_generations=2,
        advances=1,
        retries=0,
        rollbacks=1,
        best_score=0.78,
        best_elo=1200.0,
        total_duration_seconds=120.0,
        total_tokens=30000,
        total_cost_usd=0.15,
        tool_invocations=10,
        validation_failures=2,
        consultation_count=0,
        consultation_cost_usd=0.0,
        friction_signals=[
            FrictionSignal(
                signal_type="validation_failure",
                severity="medium",
                generation_index=1,
                description="Parse failure",
                evidence=["ev-1"],
            )
        ],
        delight_signals=[
            DelightSignal(
                signal_type="strong_improvement",
                generation_index=2,
                description="Big jump",
                evidence=["ev-2"],
            )
        ],
        events=[],
        metadata={},
        created_at="2026-03-16T12:00:00Z",
    )


def _seed_run(store: SQLiteStore, artifacts: ArtifactStore) -> None:
    store.create_run("run-42", "grid_ctf", 2, "local", agent_provider="anthropic")
    store.upsert_generation(
        run_id="run-42",
        generation_index=1,
        mean_score=0.55,
        best_score=0.55,
        elo=1100.0,
        wins=3,
        losses=1,
        gate_decision="accepted",
        status="completed",
    )
    store.upsert_generation(
        run_id="run-42",
        generation_index=2,
        mean_score=0.78,
        best_score=0.78,
        elo=1200.0,
        wins=4,
        losses=0,
        gate_decision="accepted",
        status="completed",
    )
    store.append_agent_output("run-42", 1, "competitor", '{"aggression": 0.7, "defense": 0.4}')
    store.append_agent_output("run-42", 2, "competitor", '{"aggression": 0.8, "defense": 0.4}')
    store.mark_run_completed("run-42")
    FacetStore(artifacts.knowledge_root).persist(_make_facet())
    artifacts.write_progress_report(
        "grid_ctf",
        "run-42",
        RunProgressReport(
            run_id="run-42",
            scenario="grid_ctf",
            total_generations=2,
            advances=1,
            rollbacks=1,
            retries=0,
            progress=NormalizedProgress(
                raw_score=0.78,
                normalized_score=0.78,
                score_floor=0.0,
                score_ceiling=1.0,
                pct_of_ceiling=78.0,
            ),
            cost=CostEfficiency(
                total_input_tokens=20000,
                total_output_tokens=10000,
                total_tokens=30000,
                total_cost_usd=0.15,
            ),
        ),
    )
    artifacts.write_weakness_report(
        "grid_ctf",
        "run-42",
        WeaknessReport(
            run_id="run-42",
            scenario="grid_ctf",
            total_generations=2,
            weaknesses=[
                Weakness(
                    category="validation_failure",
                    severity="medium",
                    affected_generations=[1],
                    description="Parse failure on generation 1",
                    evidence={"count": 1},
                    frequency=1,
                )
            ],
        ),
    )


@pytest.fixture()
def hub_api_env(tmp_path: Path) -> dict[str, Any]:
    from autocontext.server.app import create_app

    env = {
        "AUTOCONTEXT_DB_PATH": str(tmp_path / "test.db"),
        "AUTOCONTEXT_RUNS_ROOT": str(tmp_path / "runs"),
        "AUTOCONTEXT_KNOWLEDGE_ROOT": str(tmp_path / "knowledge"),
        "AUTOCONTEXT_SKILLS_ROOT": str(tmp_path / "skills"),
        "AUTOCONTEXT_CLAUDE_SKILLS_PATH": str(tmp_path / ".claude" / "skills"),
        "AUTOCONTEXT_EVENT_STREAM_PATH": str(tmp_path / "events.ndjson"),
    }
    for key, value in env.items():
        os.environ[key] = value

    try:
        app = create_app(allow_insecure_test_principal=True)
        client = TestClient(app)
        store: SQLiteStore = app.state.store
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        yield {
            "client": client,
            "store": store,
            "artifacts": artifacts,
        }
    finally:
        for key in env:
            os.environ.pop(key, None)


def test_put_and_list_hub_sessions(hub_api_env: dict[str, Any]) -> None:
    client: TestClient = hub_api_env["client"]

    put_resp = client.put(
        "/api/hub/sessions/sess-1",
        json={
            "scenario_name": "grid_ctf",
            "owner": "alice",
            "shared": True,
            "current_objective": "Maximize flag captures",
            "current_hypotheses": ["High aggression works above 0.6 density"],
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["owner"] == "alice"

    list_resp = client.get("/api/hub/sessions")
    assert list_resp.status_code == 200
    sessions = list_resp.json()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "sess-1"

    heartbeat_resp = client.post("/api/hub/sessions/sess-1/heartbeat", json={"lease_seconds": 300})
    assert heartbeat_resp.status_code == 200
    assert heartbeat_resp.json()["lease_expires_at"] != ""


def test_package_result_and_feed_endpoints_are_live(hub_api_env: dict[str, Any]) -> None:
    client: TestClient = hub_api_env["client"]
    store: SQLiteStore = hub_api_env["store"]
    artifacts: ArtifactStore = hub_api_env["artifacts"]
    _seed_run(store, artifacts)

    client.put(
        "/api/hub/sessions/sess-1",
        json={
            "scenario_name": "grid_ctf",
            "owner": "alice",
            "best_run_id": "run-42",
            "best_generation": 2,
            "best_score": 0.78,
            "current_hypotheses": ["High aggression works above 0.6 density"],
        },
    )

    package_resp = client.post(
        "/api/hub/packages/from-run/run-42",
        json={"session_id": "sess-1", "actor": "alice"},
    )
    assert package_resp.status_code == 200
    package = package_resp.json()
    assert package["source_run_id"] == "run-42"

    result_resp = client.post(
        "/api/hub/results/from-run/run-42",
        json={"package_id": package["package_id"]},
    )
    assert result_resp.status_code == 200
    result = result_resp.json()
    assert result["run_id"] == "run-42"
    assert "Parse failure" in result["weakness_summary"]

    adopt_resp = client.post(
        f"/api/hub/packages/{package['package_id']}/adopt",
        json={"actor": "bob", "conflict_policy": "merge"},
    )
    assert adopt_resp.status_code == 200
    assert adopt_resp.json()["import_result"]["scenario_name"] == "grid_ctf"

    packages_resp = client.get("/api/hub/packages")
    results_resp = client.get("/api/hub/results")
    feed_resp = client.get("/api/hub/feed")
    assert packages_resp.status_code == 200
    assert results_resp.status_code == 200
    assert feed_resp.status_code == 200
    assert len(packages_resp.json()) == 1
    assert len(results_resp.json()) == 1
    assert len(feed_resp.json()["promotions"]) >= 2
