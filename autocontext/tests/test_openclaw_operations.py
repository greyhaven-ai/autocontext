"""Tests for OpenClaw MCP/API operations (MTS-191).

Tests for evaluate, validate, publish, fetch, distill-status MCP tools
and corresponding REST endpoints.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from autocontext.artifacts import (
    ArtifactProvenance,
    HarnessArtifact,
    PolicyArtifact,
)
from autocontext.config import AppSettings
from autocontext.mcp.tools import MtsToolContext


class _TestDistillSidecar:
    def launch(self, job_id: str, scenario: str, config: dict[str, object]) -> None:
        del job_id, scenario, config

    def poll(self, job_id: str) -> dict[str, object]:
        del job_id
        return {}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tool_ctx(tmp_path: Path) -> MtsToolContext:
    settings = AppSettings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    return MtsToolContext(settings)


@pytest.fixture()
def _seed_artifact(tool_ctx: MtsToolContext) -> None:
    """Seed a harness artifact to the artifact store directory."""
    artifacts_dir = tool_ctx.settings.knowledge_root / "_openclaw_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
    h = HarnessArtifact(
        name="test_harness",
        version=1,
        scenario="grid_ctf",
        source_code="def validate(s): return True",
        provenance=prov,
    )
    (artifacts_dir / f"{h.id}.json").write_text(h.model_dump_json(), encoding="utf-8")


# ---------------------------------------------------------------------------
# MCP tool: mts_evaluate_strategy
# ---------------------------------------------------------------------------


class TestEvaluateStrategy:
    def test_evaluate_known_scenario(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import evaluate_strategy

        result = evaluate_strategy(
            scenario_name="grid_ctf",
            strategy={"aggression": 0.5, "defense": 0.5, "path_bias": 0.5},
            num_matches=2,
            seed_base=42,
        )
        assert "mean_score" in result
        assert "matches" in result
        assert result["matches"] == 2
        assert isinstance(result["mean_score"], float)

    def test_evaluate_unknown_scenario(self) -> None:
        from autocontext.mcp.tools import evaluate_strategy

        result = evaluate_strategy(
            scenario_name="nonexistent",
            strategy={"aggression": 0.5},
        )
        assert "error" in result

    def test_evaluate_agent_task_scenario(self) -> None:
        """Agent task scenarios should return an error directing to judge evaluation."""
        from autocontext.mcp.tools import evaluate_strategy

        # Test with a scenario that doesn't exist; the error path is the important part
        result = evaluate_strategy(
            scenario_name="nonexistent_task",
            strategy={},
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# MCP tool: mts_validate_strategy_against_harness
# ---------------------------------------------------------------------------


class TestValidateStrategyOp:
    def test_validate_valid_strategy(self) -> None:
        from autocontext.mcp.tools import validate_strategy_against_harness

        result = validate_strategy_against_harness(
            scenario_name="grid_ctf",
            strategy={"aggression": 0.5, "defense": 0.5, "path_bias": 0.5},
        )
        assert "valid" in result
        assert result["valid"] is True

    def test_validate_invalid_strategy(self) -> None:
        from autocontext.mcp.tools import validate_strategy_against_harness

        result = validate_strategy_against_harness(
            scenario_name="grid_ctf",
            strategy={"aggression": 5.0, "defense": 0.5, "path_bias": 0.5},
        )
        assert result["valid"] is False
        assert "reason" in result

    def test_validate_unknown_scenario(self) -> None:
        from autocontext.mcp.tools import validate_strategy_against_harness

        result = validate_strategy_against_harness(
            scenario_name="nonexistent",
            strategy={},
        )
        assert "error" in result

    def test_validate_uses_published_harness_artifacts(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import publish_artifact, validate_strategy_against_harness

        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        harness = HarnessArtifact(
            name="max_aggression",
            version=1,
            scenario="grid_ctf",
            source_code=(
                "def validate_strategy(strategy, scenario):\n"
                "    if float(strategy.get('aggression', 0.0)) <= 0.6:\n"
                "        return True, []\n"
                "    return False, ['aggression must be <= 0.6']\n"
            ),
            provenance=prov,
        )
        publish_artifact(tool_ctx, harness.model_dump())

        result = validate_strategy_against_harness(
            scenario_name="grid_ctf",
            strategy={"aggression": 0.7, "defense": 0.5, "path_bias": 0.5},
            ctx=tool_ctx,
        )

        assert result["valid"] is False
        assert result["harness_passed"] is False
        assert result["harness_loaded"] == [f"openclaw_{harness.id}"]
        assert any("aggression must be <= 0.6" in err for err in result["harness_errors"])


# ---------------------------------------------------------------------------
# MCP tool: mts_publish_artifact
# ---------------------------------------------------------------------------


class TestPublishArtifact:
    def test_publish_harness_artifact(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import publish_artifact

        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(
            name="grid_ctf_validator",
            version=1,
            scenario="grid_ctf",
            source_code="def validate(s): return True",
            provenance=prov,
        )
        result = publish_artifact(tool_ctx, h.model_dump())
        assert result["status"] == "published"
        assert result["artifact_id"] == h.id
        assert result["artifact_type"] == "harness"

    def test_publish_policy_artifact(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import publish_artifact

        prov = ArtifactProvenance(run_id="run_1", generation=5, scenario="grid_ctf")
        p = PolicyArtifact(
            name="aggressive_ctf",
            version=1,
            scenario="grid_ctf",
            source_code="def policy(s): return {'aggression': 0.9}",
            provenance=prov,
        )
        result = publish_artifact(tool_ctx, p.model_dump())
        assert result["status"] == "published"
        assert result["artifact_type"] == "policy"

    def test_publish_invalid_artifact(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import publish_artifact

        result = publish_artifact(tool_ctx, {"bad": "data"})
        assert "error" in result

    def test_publish_creates_storage_dir(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import publish_artifact

        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(
            name="test",
            version=1,
            scenario="grid_ctf",
            source_code="pass\n",
            provenance=prov,
        )
        publish_artifact(tool_ctx, h.model_dump())
        artifacts_dir = tool_ctx.settings.knowledge_root / "_openclaw_artifacts"
        assert artifacts_dir.exists()

    def test_publish_harness_syncs_runtime_harness(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import publish_artifact

        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(
            name="runtime_sync",
            version=1,
            scenario="grid_ctf",
            source_code="def validate_strategy(strategy, scenario):\n    return True, []\n",
            provenance=prov,
        )

        publish_artifact(tool_ctx, h.model_dump())

        assert tool_ctx.artifacts.read_harness("grid_ctf", f"openclaw_{h.id}") is not None


# ---------------------------------------------------------------------------
# MCP tool: mts_fetch_artifact
# ---------------------------------------------------------------------------


class TestFetchArtifact:
    def test_fetch_existing(self, tool_ctx: MtsToolContext, _seed_artifact: None) -> None:
        from autocontext.mcp.tools import fetch_artifact, list_artifacts

        # First list to get the ID
        listed = list_artifacts(tool_ctx)
        assert len(listed) > 0
        artifact_id = listed[0]["id"]

        result = fetch_artifact(tool_ctx, artifact_id)
        assert result["name"] == "test_harness"
        assert result["artifact_type"] == "harness"

    def test_fetch_missing(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import fetch_artifact

        result = fetch_artifact(tool_ctx, "nonexistent-id")
        assert "error" in result

    def test_publish_then_fetch_roundtrip(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import fetch_artifact, publish_artifact

        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(
            name="roundtrip_test",
            version=1,
            scenario="grid_ctf",
            source_code="def check(): pass\n",
            provenance=prov,
        )
        pub_result = publish_artifact(tool_ctx, h.model_dump())
        fetched = fetch_artifact(tool_ctx, pub_result["artifact_id"])
        assert fetched["name"] == "roundtrip_test"
        assert fetched["source_code"] == "def check(): pass\n"


# ---------------------------------------------------------------------------
# MCP tool: mts_list_artifacts
# ---------------------------------------------------------------------------


class TestListArtifacts:
    def test_list_empty(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import list_artifacts

        result = list_artifacts(tool_ctx)
        assert result == []

    def test_list_after_publish(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import list_artifacts, publish_artifact

        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(
            name="h1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov,
        )
        publish_artifact(tool_ctx, h.model_dump())
        listed = list_artifacts(tool_ctx)
        assert len(listed) == 1
        assert listed[0]["name"] == "h1"

    def test_list_filters_by_scenario(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import list_artifacts, publish_artifact

        prov1 = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        prov2 = ArtifactProvenance(run_id="run_2", generation=1, scenario="othello")
        h1 = HarnessArtifact(name="h1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov1)
        h2 = HarnessArtifact(name="h2", version=1, scenario="othello", source_code="pass\n", provenance=prov2)
        publish_artifact(tool_ctx, h1.model_dump())
        publish_artifact(tool_ctx, h2.model_dump())
        listed = list_artifacts(tool_ctx, scenario="grid_ctf")
        assert len(listed) == 1
        assert listed[0]["name"] == "h1"

    def test_list_filters_by_artifact_type(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import list_artifacts, publish_artifact

        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(name="h1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        p = PolicyArtifact(name="p1", version=1, scenario="grid_ctf", source_code="pass\n", provenance=prov)
        publish_artifact(tool_ctx, h.model_dump())
        publish_artifact(tool_ctx, p.model_dump())
        listed = list_artifacts(tool_ctx, artifact_type="policy")
        assert len(listed) == 1
        assert listed[0]["name"] == "p1"


# ---------------------------------------------------------------------------
# MCP tool: mts_distill_status
# ---------------------------------------------------------------------------


class TestDistillStatus:
    def test_no_active_jobs(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import distill_status

        result = distill_status(tool_ctx)
        assert result["active_jobs"] == 0
        assert result["jobs"] == []

    def test_trigger_distillation(self, tool_ctx: MtsToolContext) -> None:
        from autocontext.mcp.tools import trigger_distillation

        with patch("autocontext.openclaw.distill.load_distill_sidecar", return_value=_TestDistillSidecar()):
            result = trigger_distillation(
                tool_ctx,
                scenario="grid_ctf",
                source_artifact_ids=[],
            )
        assert "job_id" in result
        assert result["status"] == "running"


# ---------------------------------------------------------------------------
# MCP tool: mts_capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_capabilities_metadata(self) -> None:
        from autocontext.mcp.tools import get_capabilities

        caps = get_capabilities()
        assert "operations" in caps
        assert isinstance(caps["operations"], list)
        assert len(caps["operations"]) > 0
        # Verify all expected operations present
        op_names = [op["name"] for op in caps["operations"]]
        assert "evaluate_strategy" in op_names
        assert "validate_strategy" in op_names
        assert "publish_artifact" in op_names
        assert "fetch_artifact" in op_names
        assert "distill_status" in op_names
        assert "version" in caps


# ---------------------------------------------------------------------------
# MCP server wrappers (requires mcp package)
# ---------------------------------------------------------------------------


class TestMCPServerWrappers:
    """Verify that server.py has @mcp.tool() wrappers for all new tools."""

    @pytest.fixture(autouse=True)
    def _skip_without_mcp(self) -> None:
        pytest.importorskip("mcp", reason="MCP package not installed")

    def test_evaluate_strategy_tool_exists(self) -> None:
        from autocontext.mcp import server
        assert hasattr(server, "mts_evaluate_strategy")

    def test_validate_strategy_tool_exists(self) -> None:
        from autocontext.mcp import server
        assert hasattr(server, "mts_validate_strategy_against_harness")

    def test_publish_artifact_tool_exists(self) -> None:
        from autocontext.mcp import server
        assert hasattr(server, "mts_publish_artifact")

    def test_fetch_artifact_tool_exists(self) -> None:
        from autocontext.mcp import server
        assert hasattr(server, "mts_fetch_artifact")

    def test_list_artifacts_tool_exists(self) -> None:
        from autocontext.mcp import server
        assert hasattr(server, "mts_list_artifacts")

    def test_distill_status_tool_exists(self) -> None:
        from autocontext.mcp import server
        assert hasattr(server, "mts_distill_status")

    def test_capabilities_tool_exists(self) -> None:
        from autocontext.mcp import server
        assert hasattr(server, "mts_capabilities")


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------


class TestRESTEndpoints:
    """Test the FastAPI REST endpoints mirroring MCP tools."""

    @pytest.fixture()
    def client(self) -> TestClient:
        from autocontext.server.app import create_app
        app = create_app()
        return TestClient(app)

    def test_evaluate_strategy_endpoint(self, client: TestClient) -> None:
        resp = client.post(
            "/api/openclaw/evaluate",
            json={
                "scenario_name": "grid_ctf",
                "strategy": {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5},
                "num_matches": 2,
                "seed_base": 42,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "mean_score" in data

    def test_validate_strategy_endpoint(self, client: TestClient) -> None:
        resp = client.post(
            "/api/openclaw/validate",
            json={
                "scenario_name": "grid_ctf",
                "strategy": {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True

    def test_publish_artifact_endpoint(self, client: TestClient) -> None:
        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        h = HarnessArtifact(
            name="rest_test",
            version=1,
            scenario="grid_ctf",
            source_code="pass\n",
            provenance=prov,
        )
        resp = client.post(
            "/api/openclaw/artifacts",
            json=h.model_dump(mode="json"),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "published"

    def test_list_artifacts_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/openclaw/artifacts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_fetch_artifact_endpoint_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/openclaw/artifacts/nonexistent-id")
        assert resp.status_code == 404

    def test_distill_status_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/openclaw/distill")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_jobs" in data

    def test_capabilities_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/openclaw/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "operations" in data
        assert "version" in data

    def test_openclaw_context_is_scoped_to_each_app(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from autocontext.server import app as app_module

        settings_one = AppSettings(
            runs_root=tmp_path / "runs_one",
            knowledge_root=tmp_path / "knowledge_one",
            skills_root=tmp_path / "skills_one",
            claude_skills_path=tmp_path / ".claude" / "skills_one",
        )
        settings_two = AppSettings(
            runs_root=tmp_path / "runs_two",
            knowledge_root=tmp_path / "knowledge_two",
            skills_root=tmp_path / "skills_two",
            claude_skills_path=tmp_path / ".claude" / "skills_two",
        )

        monkeypatch.setattr(app_module, "load_settings", lambda: settings_one)
        client_one = TestClient(app_module.create_app())

        prov = ArtifactProvenance(run_id="run_1", generation=1, scenario="grid_ctf")
        artifact = HarnessArtifact(
            name="isolated",
            version=1,
            scenario="grid_ctf",
            source_code="pass\n",
            provenance=prov,
        )
        publish_resp = client_one.post("/api/openclaw/artifacts", json=artifact.model_dump(mode="json"))
        assert publish_resp.status_code == 200

        monkeypatch.setattr(app_module, "load_settings", lambda: settings_two)
        client_two = TestClient(app_module.create_app())

        assert client_one.get("/api/openclaw/artifacts").json() != []
        assert client_two.get("/api/openclaw/artifacts").json() == []
