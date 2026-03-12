"""Tests for the OpenClaw discovery and capability advertisement module (AC-195)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autocontext.config.settings import AppSettings, HarnessMode
from autocontext.storage.artifacts import EMPTY_PLAYBOOK_SENTINEL

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> AppSettings:
    """Minimal AppSettings pointing at temporary directories."""
    return AppSettings(
        db_path=tmp_path / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        executor_mode="local",
        agent_provider="anthropic",
        harness_mode=HarnessMode.NONE,
        rlm_enabled=False,
    )


@pytest.fixture()
def mock_ctx(tmp_settings: AppSettings, tmp_path: Path) -> MagicMock:
    """A mock MtsToolContext with realistic structure."""
    ctx = MagicMock()
    ctx.settings = tmp_settings
    ctx.artifacts = MagicMock()
    ctx.artifacts.knowledge_root = tmp_settings.knowledge_root
    ctx.sqlite = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# ScenarioCapabilities
# ---------------------------------------------------------------------------


class TestScenarioCapabilities:
    """discover_scenario_capabilities should detect harness, playbook, and evaluation mode."""

    def test_game_scenario_detected(self, mock_ctx: MagicMock) -> None:
        """A game scenario (grid_ctf) should report tournament evaluation mode."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        caps = discover_scenario_capabilities(mock_ctx, "grid_ctf")
        assert caps.evaluation_mode == "tournament"
        assert caps.scenario_name == "grid_ctf"

    def test_has_playbook_when_present(self, mock_ctx: MagicMock) -> None:
        """has_playbook should be True when a playbook file exists."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        mock_ctx.artifacts.read_playbook.return_value = "# Some playbook content"
        caps = discover_scenario_capabilities(mock_ctx, "grid_ctf")
        assert caps.has_playbook is True

    def test_no_playbook_when_empty(self, mock_ctx: MagicMock) -> None:
        """has_playbook should be False when playbook is empty."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        mock_ctx.artifacts.read_playbook.return_value = ""
        caps = discover_scenario_capabilities(mock_ctx, "grid_ctf")
        assert caps.has_playbook is False

    def test_no_playbook_when_sentinel(self, mock_ctx: MagicMock) -> None:
        """has_playbook should be False when ArtifactStore returns the empty sentinel."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        mock_ctx.artifacts.read_playbook.return_value = EMPTY_PLAYBOOK_SENTINEL
        caps = discover_scenario_capabilities(mock_ctx, "grid_ctf")
        assert caps.has_playbook is False

    def test_has_harness_when_dir_has_files(self, mock_ctx: MagicMock, tmp_path: Path) -> None:
        """has_harness and harness_count should reflect harness files on disk."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        harness_dir = tmp_path / "knowledge" / "grid_ctf" / "harness"
        harness_dir.mkdir(parents=True)
        (harness_dir / "test_harness.py").write_text("def validate(): pass")
        mock_ctx.artifacts.harness_dir.return_value = harness_dir

        caps = discover_scenario_capabilities(mock_ctx, "grid_ctf")
        assert caps.has_harness is True
        assert caps.harness_count == 1

    def test_no_harness_when_empty(self, mock_ctx: MagicMock, tmp_path: Path) -> None:
        """has_harness should be False when no harness directory or no files."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        harness_dir = tmp_path / "knowledge" / "grid_ctf" / "harness"
        mock_ctx.artifacts.harness_dir.return_value = harness_dir
        # directory does not exist

        caps = discover_scenario_capabilities(mock_ctx, "grid_ctf")
        assert caps.has_harness is False
        assert caps.harness_count == 0

    def test_has_policy_from_artifacts(self, mock_ctx: MagicMock, tmp_path: Path) -> None:
        """has_policy should be True when policy artifacts exist for the scenario."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        artifacts_dir = tmp_path / "knowledge" / "_openclaw_artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "abc123.json").write_text(
            json.dumps({"artifact_type": "policy", "scenario": "grid_ctf"})
        )
        mock_ctx.settings.knowledge_root = tmp_path / "knowledge"

        caps = discover_scenario_capabilities(mock_ctx, "grid_ctf")
        assert caps.has_policy is True

    def test_best_score_and_elo_from_db(self, mock_ctx: MagicMock) -> None:
        """best_score and best_elo should be pulled from SQLite data."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        mock_ctx.sqlite.get_best_knowledge_snapshot.return_value = {
            "best_score": 0.85,
            "best_elo": 1520.0,
        }
        caps = discover_scenario_capabilities(mock_ctx, "grid_ctf")
        assert caps.best_score == 0.85
        assert caps.best_elo == 1520.0

    def test_unknown_scenario_raises(self, mock_ctx: MagicMock) -> None:
        """Should raise KeyError for an unregistered scenario."""
        from autocontext.openclaw.discovery import discover_scenario_capabilities

        with pytest.raises(KeyError, match="unknown_scenario"):
            discover_scenario_capabilities(mock_ctx, "unknown_scenario")


# ---------------------------------------------------------------------------
# RuntimeHealth
# ---------------------------------------------------------------------------


class TestRuntimeHealth:
    """get_runtime_health should read current config state."""

    def test_reads_executor_mode(self, tmp_settings: AppSettings) -> None:
        from autocontext.openclaw.discovery import get_runtime_health

        health = get_runtime_health(tmp_settings)
        assert health.executor_mode == "local"

    def test_reads_agent_provider(self, tmp_settings: AppSettings) -> None:
        from autocontext.openclaw.discovery import get_runtime_health

        health = get_runtime_health(tmp_settings)
        assert health.agent_provider == "anthropic"

    def test_reads_harness_mode(self, tmp_settings: AppSettings) -> None:
        from autocontext.openclaw.discovery import get_runtime_health

        health = get_runtime_health(tmp_settings)
        assert health.harness_mode == "none"

    def test_reads_rlm_enabled(self, tmp_settings: AppSettings) -> None:
        from autocontext.openclaw.discovery import get_runtime_health

        health = get_runtime_health(tmp_settings)
        assert health.rlm_enabled is False

    def test_available_models_includes_roles(self, tmp_settings: AppSettings) -> None:
        from autocontext.openclaw.discovery import get_runtime_health

        health = get_runtime_health(tmp_settings)
        assert "competitor" in health.available_models
        assert "analyst" in health.available_models
        assert "coach" in health.available_models
        assert "architect" in health.available_models
        assert "judge" in health.available_models

    def test_serializes_to_dict(self, tmp_settings: AppSettings) -> None:
        from autocontext.openclaw.discovery import get_runtime_health

        health = get_runtime_health(tmp_settings)
        d = health.model_dump()
        assert isinstance(d, dict)
        assert "executor_mode" in d
        assert "available_models" in d


# ---------------------------------------------------------------------------
# CapabilityAdvertisement
# ---------------------------------------------------------------------------


class TestCapabilityAdvertisement:
    """advertise_capabilities should combine runtime + scenarios + artifacts."""

    def test_includes_version(self, mock_ctx: MagicMock) -> None:
        from autocontext.openclaw.discovery import advertise_capabilities

        ad = advertise_capabilities(mock_ctx)
        assert ad.version is not None
        assert isinstance(ad.version, str)

    def test_includes_runtime_health(self, mock_ctx: MagicMock) -> None:
        from autocontext.openclaw.discovery import advertise_capabilities

        ad = advertise_capabilities(mock_ctx)
        assert ad.runtime_health is not None
        assert ad.runtime_health.executor_mode == mock_ctx.settings.executor_mode

    def test_includes_scenario_capabilities(self, mock_ctx: MagicMock) -> None:
        from autocontext.openclaw.discovery import advertise_capabilities

        ad = advertise_capabilities(mock_ctx)
        # Should include registered scenarios
        assert isinstance(ad.scenario_capabilities, dict)
        assert "grid_ctf" in ad.scenario_capabilities
        assert "othello" in ad.scenario_capabilities

    def test_includes_artifact_counts(self, mock_ctx: MagicMock, tmp_path: Path) -> None:
        from autocontext.openclaw.discovery import advertise_capabilities

        # Set up artifacts directory with mixed types
        artifacts_dir = tmp_path / "knowledge" / "_openclaw_artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "h1.json").write_text(json.dumps({"artifact_type": "harness", "scenario": "grid_ctf"}))
        (artifacts_dir / "p1.json").write_text(json.dumps({"artifact_type": "policy", "scenario": "grid_ctf"}))
        (artifacts_dir / "p2.json").write_text(json.dumps({"artifact_type": "policy", "scenario": "othello"}))
        mock_ctx.settings.knowledge_root = tmp_path / "knowledge"

        ad = advertise_capabilities(mock_ctx)
        assert ad.artifact_counts["harness"] == 1
        assert ad.artifact_counts["policy"] == 2

    def test_empty_artifact_counts(self, mock_ctx: MagicMock) -> None:
        from autocontext.openclaw.discovery import advertise_capabilities

        ad = advertise_capabilities(mock_ctx)
        # With no artifacts directory, counts should be 0
        assert ad.artifact_counts.get("harness", 0) == 0
        assert ad.artifact_counts.get("policy", 0) == 0

    def test_serializes_to_dict(self, mock_ctx: MagicMock) -> None:
        from autocontext.openclaw.discovery import advertise_capabilities

        ad = advertise_capabilities(mock_ctx)
        d = ad.model_dump()
        assert isinstance(d, dict)
        assert "version" in d
        assert "runtime_health" in d
        assert "scenario_capabilities" in d
        assert "artifact_counts" in d


# ---------------------------------------------------------------------------
# ScenarioArtifactLookup
# ---------------------------------------------------------------------------


class TestScenarioArtifactLookup:
    """scenario_artifact_lookup should filter artifacts by scenario."""

    def test_returns_only_matching_scenario(self, mock_ctx: MagicMock, tmp_path: Path) -> None:
        from autocontext.openclaw.discovery import scenario_artifact_lookup

        artifacts_dir = tmp_path / "knowledge" / "_openclaw_artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "h1.json").write_text(json.dumps({
            "id": "h1", "name": "test_harness", "artifact_type": "harness",
            "scenario": "grid_ctf", "version": 1,
        }))
        (artifacts_dir / "p1.json").write_text(json.dumps({
            "id": "p1", "name": "test_policy", "artifact_type": "policy",
            "scenario": "othello", "version": 1,
        }))
        mock_ctx.settings.knowledge_root = tmp_path / "knowledge"

        results = scenario_artifact_lookup(mock_ctx, "grid_ctf")
        assert len(results) == 1
        assert results[0].artifact_id == "h1"
        assert results[0].artifact_type == "harness"

    def test_empty_when_no_artifacts(self, mock_ctx: MagicMock) -> None:
        from autocontext.openclaw.discovery import scenario_artifact_lookup

        results = scenario_artifact_lookup(mock_ctx, "grid_ctf")
        assert results == []

    def test_returns_all_types_for_scenario(self, mock_ctx: MagicMock, tmp_path: Path) -> None:
        from autocontext.openclaw.discovery import scenario_artifact_lookup

        artifacts_dir = tmp_path / "knowledge" / "_openclaw_artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "h1.json").write_text(json.dumps({
            "id": "h1", "name": "harness", "artifact_type": "harness",
            "scenario": "grid_ctf", "version": 1,
        }))
        (artifacts_dir / "p1.json").write_text(json.dumps({
            "id": "p1", "name": "policy", "artifact_type": "policy",
            "scenario": "grid_ctf", "version": 2,
        }))
        mock_ctx.settings.knowledge_root = tmp_path / "knowledge"

        results = scenario_artifact_lookup(mock_ctx, "grid_ctf")
        assert len(results) == 2
        types = {r.artifact_type for r in results}
        assert types == {"harness", "policy"}

    def test_artifact_summary_fields(self, mock_ctx: MagicMock, tmp_path: Path) -> None:
        from autocontext.openclaw.discovery import scenario_artifact_lookup

        artifacts_dir = tmp_path / "knowledge" / "_openclaw_artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "h1.json").write_text(json.dumps({
            "id": "h1", "name": "test_harness", "artifact_type": "harness",
            "scenario": "grid_ctf", "version": 3,
        }))
        mock_ctx.settings.knowledge_root = tmp_path / "knowledge"

        results = scenario_artifact_lookup(mock_ctx, "grid_ctf")
        assert len(results) == 1
        summary = results[0]
        assert summary.artifact_id == "h1"
        assert summary.name == "test_harness"
        assert summary.artifact_type == "harness"
        assert summary.scenario == "grid_ctf"
        assert summary.version == 3


# ---------------------------------------------------------------------------
# MCP tool functions
# ---------------------------------------------------------------------------


class TestMcpToolFunctions:
    """Thin MCP tool wrappers in tools.py should delegate to discovery module."""

    def test_skill_advertise_capabilities(self, mock_ctx: MagicMock) -> None:
        from autocontext.mcp.tools import skill_advertise_capabilities

        result = skill_advertise_capabilities(mock_ctx)
        assert isinstance(result, dict)
        assert "version" in result
        assert "runtime_health" in result
        assert "scenario_capabilities" in result

    def test_skill_scenario_capabilities(self, mock_ctx: MagicMock) -> None:
        from autocontext.mcp.tools import skill_scenario_capabilities

        result = skill_scenario_capabilities(mock_ctx, "grid_ctf")
        assert isinstance(result, dict)
        assert result["scenario_name"] == "grid_ctf"
        assert result["evaluation_mode"] == "tournament"

    def test_skill_runtime_health(self, mock_ctx: MagicMock) -> None:
        from autocontext.mcp.tools import skill_runtime_health

        result = skill_runtime_health(mock_ctx)
        assert isinstance(result, dict)
        assert "executor_mode" in result
        assert "agent_provider" in result

    def test_skill_scenario_artifact_lookup(self, mock_ctx: MagicMock) -> None:
        from autocontext.mcp.tools import skill_scenario_artifact_lookup

        result = skill_scenario_artifact_lookup(mock_ctx, "grid_ctf")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


class TestRestEndpoints:
    """The /api/openclaw/discovery/* endpoints should work end-to-end."""

    @pytest.fixture()
    def client(self, tmp_settings: AppSettings) -> object:
        """Create a test client for the FastAPI app."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from autocontext.server.openclaw_api import router
        app = FastAPI()
        app.include_router(router)

        # Inject settings so get_openclaw_ctx can create a real context
        app.state.app_settings = tmp_settings

        return TestClient(app)

    def test_capabilities_endpoint(self, client: object) -> None:
        from fastapi.testclient import TestClient

        resp = TestClient.__dict__  # just for type reference
        c: TestClient = client  # type: ignore[assignment]
        resp = c.get("/api/openclaw/discovery/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "runtime_health" in data
        assert "scenario_capabilities" in data

    def test_scenario_capabilities_endpoint(self, client: object) -> None:
        from fastapi.testclient import TestClient

        c: TestClient = client  # type: ignore[assignment]
        resp = c.get("/api/openclaw/discovery/scenario/grid_ctf")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scenario_name"] == "grid_ctf"
        assert data["evaluation_mode"] == "tournament"

    def test_scenario_not_found(self, client: object) -> None:
        from fastapi.testclient import TestClient

        c: TestClient = client  # type: ignore[assignment]
        resp = c.get("/api/openclaw/discovery/scenario/nonexistent")
        assert resp.status_code == 404

    def test_health_endpoint(self, client: object) -> None:
        from fastapi.testclient import TestClient

        c: TestClient = client  # type: ignore[assignment]
        resp = c.get("/api/openclaw/discovery/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "executor_mode" in data
        assert "agent_provider" in data

    def test_scenario_artifacts_endpoint(self, client: object) -> None:
        from fastapi.testclient import TestClient

        c: TestClient = client  # type: ignore[assignment]
        resp = c.get("/api/openclaw/discovery/scenario/grid_ctf/artifacts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
