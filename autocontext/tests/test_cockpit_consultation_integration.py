"""Tests for AC-220: Wire explicit operator-requested consultation from cockpit.

TDD integration tests for POST /api/cockpit/runs/{run_id}/consult and
GET /api/cockpit/runs/{run_id}/consultations endpoints.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.config.settings import AppSettings
from autocontext.providers.base import CompletionResult
from autocontext.server.cockpit_api import cockpit_router
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# Response text that ConsultationRunner can parse into sections
MOCK_RESPONSE_TEXT = (
    "## Critique\nTest critique content\n\n"
    "## Alternative Hypothesis\nTest alternative hypothesis\n\n"
    "## Tiebreak Recommendation\nTest tiebreak recommendation\n\n"
    "## Suggested Next Action\nTest suggested action"
)


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


def _seed_run(store: SQLiteStore, run_id: str = "test-run", gens: int = 2) -> None:
    """Create a run with completed generations for testing."""
    store.create_run(run_id, "grid_ctf", 5, "local")
    store.upsert_generation(run_id, 1, 0.4, 0.5, 1000.0, 2, 1, "advance", "completed", 30.0)
    if gens >= 2:
        store.upsert_generation(run_id, 2, 0.6, 0.7, 1050.0, 3, 0, "advance", "completed", 45.0)


def _make_settings(tmp_path: Path, **overrides: Any) -> AppSettings:
    """Build AppSettings with consultation enabled by default."""
    defaults: dict[str, Any] = {
        "db_path": tmp_path / "test.db",
        "runs_root": tmp_path / "runs",
        "knowledge_root": tmp_path / "knowledge",
        "skills_root": tmp_path / "skills",
        "claude_skills_path": tmp_path / ".claude" / "skills",
        "event_stream_path": tmp_path / "events.ndjson",
        "consultation_enabled": True,
        "consultation_provider": "anthropic",
        "consultation_api_key": "test-key-fake",
        "consultation_model": "test-model",
        "consultation_cost_budget": 0.0,
    }
    defaults.update(overrides)
    return AppSettings(**defaults)


@pytest.fixture()
def cockpit_consultation_env(tmp_path: Path) -> Generator[dict[str, Any], None, None]:
    """Build a FastAPI app with consultation-enabled settings."""
    store = _make_store(tmp_path)
    artifacts = _make_artifacts(tmp_path)
    settings = _make_settings(tmp_path)

    app = FastAPI()
    app.state.store = store
    app.state.app_settings = settings
    app.include_router(cockpit_router)
    client = TestClient(app)

    yield {
        "store": store,
        "artifacts": artifacts,
        "client": client,
        "settings": settings,
        "tmp_path": tmp_path,
        "app": app,
    }


def _mock_create_provider() -> MagicMock:
    """Return a mock provider that returns a parseable consultation response."""
    mock_provider = MagicMock()
    mock_provider.complete.return_value = CompletionResult(
        text=MOCK_RESPONSE_TEXT,
        model="mock-model",
        cost_usd=0.01,
    )
    mock_provider.default_model.return_value = "mock-model"
    return mock_provider


# ---------------------------------------------------------------------------
# POST /api/cockpit/runs/{run_id}/consult
# ---------------------------------------------------------------------------


class TestConsultEndpointSuccess:
    """Successful consultation with mock provider."""

    def test_successful_consultation(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "test-run"
        assert data["trigger"] == "operator_request"
        assert data["critique"] == "Test critique content"
        assert data["alternative_hypothesis"] == "Test alternative hypothesis"
        assert data["tiebreak_recommendation"] == "Test tiebreak recommendation"
        assert data["suggested_next_action"] == "Test suggested action"
        assert data["model_used"] == "mock-model"
        assert data["cost_usd"] == 0.01
        assert isinstance(data["consultation_id"], int)
        assert data["consultation_id"] > 0

    def test_response_includes_advisory_markdown(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={})

        data = resp.json()
        assert "advisory_markdown" in data
        md = data["advisory_markdown"]
        assert "## Critique" in md
        assert "Test critique content" in md


class TestConsultEndpointDisabled:
    """POST with consultation_enabled=False returns 400."""

    def test_consultation_disabled(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        settings = _make_settings(tmp_path, consultation_enabled=False)
        _seed_run(store)

        app = FastAPI()
        app.state.store = store
        app.state.app_settings = settings
        app.include_router(cockpit_router)
        client = TestClient(app)

        resp = client.post("/api/cockpit/runs/test-run/consult", json={})
        assert resp.status_code == 400
        assert "not enabled" in resp.json()["detail"].lower()


class TestConsultEndpointNotFound:
    """POST with nonexistent run returns 404."""

    def test_nonexistent_run(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        resp = env["client"].post("/api/cockpit/runs/nonexistent/consult", json={})
        assert resp.status_code == 404
        assert "nonexistent" in resp.json()["detail"]


class TestConsultEndpointBudgetExceeded:
    """POST with budget exceeded returns 429."""

    def test_budget_exceeded(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        settings = _make_settings(tmp_path, consultation_cost_budget=0.05)
        _seed_run(store)

        app = FastAPI()
        app.state.store = store
        app.state.app_settings = settings
        app.include_router(cockpit_router)
        client = TestClient(app)

        # Insert a consultation that used up the budget
        store.insert_consultation(
            run_id="test-run",
            generation_index=1,
            trigger="stagnation",
            context_summary="prior consultation",
            critique="old critique",
            alternative_hypothesis="old alt",
            tiebreak_recommendation="old rec",
            suggested_next_action="old action",
            raw_response="raw",
            model_used="prior-model",
            cost_usd=0.05,
        )

        resp = client.post("/api/cockpit/runs/test-run/consult", json={})
        assert resp.status_code == 429
        assert "budget" in resp.json()["detail"].lower()


class TestConsultEndpointNoApiKey:
    """POST without API key returns 503."""

    def test_no_api_key(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        settings = _make_settings(tmp_path, consultation_api_key="")
        _seed_run(store)

        app = FastAPI()
        app.state.store = store
        app.state.app_settings = settings
        app.include_router(cockpit_router)
        client = TestClient(app)

        resp = client.post("/api/cockpit/runs/test-run/consult", json={})
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()


class TestConsultEndpointSpecificGeneration:
    """POST with specific generation uses provided generation."""

    def test_specific_generation(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={"generation": 1})

        assert resp.status_code == 200
        data = resp.json()
        assert data["generation"] == 1

    def test_missing_generation_returns_404(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={"generation": 99})

        assert resp.status_code == 404
        assert "generation 99" in resp.json()["detail"].lower()


class TestConsultEndpointDefaultGeneration:
    """POST without generation defaults to latest."""

    def test_defaults_to_latest_generation(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["generation"] == 2  # latest of 2 seeded generations

    def test_no_generations_returns_400(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        env["store"].create_run("empty-run", "grid_ctf", 5, "local")

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/empty-run/consult", json={})

        assert resp.status_code == 400
        assert "no generations yet" in resp.json()["detail"].lower()


class TestConsultEndpointContextSummary:
    """POST with context_summary uses operator-provided context."""

    def test_custom_context_summary(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post(
                "/api/cockpit/runs/test-run/consult",
                json={"context_summary": "Why is my score stuck at 0.7?"},
            )

        assert resp.status_code == 200
        # Verify the provider was called with the custom context in the user prompt
        call_args = mock_provider.complete.call_args
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs.get("user_prompt", "")
        assert "Why is my score stuck at 0.7?" in user_prompt

    def test_uses_latest_competitor_output_for_strategy_summary(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])
        env["store"].append_agent_output("test-run", 2, "competitor", '{"aggression": 0.2}')
        env["store"].append_agent_output("test-run", 2, "competitor", '{"aggression": 0.9}')

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={})

        assert resp.status_code == 200
        call_args = mock_provider.complete.call_args
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs.get("user_prompt", "")
        assert '"aggression": 0.9' in user_prompt


class TestConsultEndpointPersistence:
    """Verify row in consultation_log after POST."""

    def test_consultation_persisted(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={})

        assert resp.status_code == 200

        rows = env["store"].get_consultations_for_run("test-run")
        assert len(rows) == 1
        row = rows[0]
        assert row["run_id"] == "test-run"
        assert row["trigger"] == "operator_request"
        assert row["critique"] == "Test critique content"
        assert row["model_used"] == "mock-model"
        assert row["cost_usd"] == 0.01


class TestConsultEndpointArtifact:
    """Verify advisory markdown file written."""

    def test_advisory_artifact_written(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={})

        assert resp.status_code == 200

        # Check that the advisory markdown file was created
        advisory_path = (
            env["tmp_path"] / "runs" / "test-run" / "generations" / "gen_2" / "consultation.md"
        )
        assert advisory_path.exists()
        content = advisory_path.read_text(encoding="utf-8")
        assert "## Critique" in content
        assert "Test critique content" in content

    def test_existing_consultation_artifact_is_appended(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])
        advisory_path = env["tmp_path"] / "runs" / "test-run" / "generations" / "gen_2" / "consultation.md"
        advisory_path.parent.mkdir(parents=True, exist_ok=True)
        advisory_path.write_text("## Critique\nExisting automatic consultation\n", encoding="utf-8")

        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={})

        assert resp.status_code == 200
        content = advisory_path.read_text(encoding="utf-8")
        assert "Existing automatic consultation" in content
        assert "Operator Requested Consultation" in content


class TestConsultEndpointProviderFailure:
    """Returns 502 when provider raises."""

    def test_provider_failure(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        mock_provider = _mock_create_provider()
        mock_provider.complete.side_effect = RuntimeError("API connection failed")

        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            resp = env["client"].post("/api/cockpit/runs/test-run/consult", json={})

        assert resp.status_code == 502
        assert "failed" in resp.json()["detail"].lower()


class TestConsultEndpointNoSettings:
    """POST without app settings returns 500."""

    def test_missing_settings(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        _seed_run(store)

        app = FastAPI()
        app.state.store = store
        # Deliberately NOT setting app_settings
        app.include_router(cockpit_router)
        client = TestClient(app)

        resp = client.post("/api/cockpit/runs/test-run/consult", json={})
        assert resp.status_code == 500
        assert "settings" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/cockpit/runs/{run_id}/consultations
# ---------------------------------------------------------------------------


class TestListConsultations:
    """List all consultations for a run."""

    def test_list_consultations_after_creating(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        _seed_run(env["store"])

        # Create two consultations via POST
        mock_provider = _mock_create_provider()
        with patch("autocontext.server.cockpit_api._create_cockpit_consultation_provider", return_value=mock_provider):
            env["client"].post("/api/cockpit/runs/test-run/consult", json={"generation": 1})
            env["client"].post("/api/cockpit/runs/test-run/consult", json={"generation": 2})

        resp = env["client"].get("/api/cockpit/runs/test-run/consultations")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["generation_index"] == 1
        assert data[1]["generation_index"] == 2

    def test_list_consultations_empty(self, cockpit_consultation_env: dict[str, Any]) -> None:
        env = cockpit_consultation_env
        resp = env["client"].get("/api/cockpit/runs/test-run/consultations")
        assert resp.status_code == 200
        assert resp.json() == []
