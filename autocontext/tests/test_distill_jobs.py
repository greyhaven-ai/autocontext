"""Tests for AC-208: Wire OpenClaw distill endpoints to real distillation sidecar jobs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.config.settings import AppSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        knowledge_root=tmp_path / "knowledge",
        db_path=tmp_path / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
    )


def _ctx(tmp_path: Path) -> Any:
    from autocontext.mcp.tools import MtsToolContext

    return MtsToolContext(_settings(tmp_path))


class _FakeSidecar:
    def __init__(self) -> None:
        self.launched: list[tuple[str, str, dict[str, Any]]] = []
        self.poll_results: dict[str, dict[str, Any]] = {}

    def launch(self, job_id: str, scenario: str, config: dict[str, Any]) -> None:
        self.launched.append((job_id, scenario, dict(config)))

    def poll(self, job_id: str) -> dict[str, Any]:
        return dict(self.poll_results.get(job_id, {}))


@pytest.fixture
def fake_sidecar(monkeypatch: pytest.MonkeyPatch) -> _FakeSidecar:
    from autocontext.openclaw import distill as distill_module

    sidecar = _FakeSidecar()
    monkeypatch.setattr(distill_module, "load_distill_sidecar", lambda *args, **kwargs: sidecar)
    return sidecar


# ---------------------------------------------------------------------------
# TestDistillJobModel
# ---------------------------------------------------------------------------


class TestDistillJobModel:
    def test_create_pending_job(self) -> None:
        from autocontext.openclaw.distill import DistillJob

        job = DistillJob(scenario="grid_ctf")
        assert job.status == "pending"
        assert job.job_id != ""
        assert job.scenario == "grid_ctf"
        assert job.source_artifact_ids == []
        assert job.created_at != ""
        assert job.started_at is None
        assert job.completed_at is None
        assert job.error_message is None
        assert job.result_artifact_id is None

    def test_job_with_source_artifacts(self) -> None:
        from autocontext.openclaw.distill import DistillJob

        job = DistillJob(scenario="othello", source_artifact_ids=["a1", "a2"])
        assert job.source_artifact_ids == ["a1", "a2"]

    def test_job_roundtrip_json(self) -> None:
        from autocontext.openclaw.distill import DistillJob

        job = DistillJob(scenario="grid_ctf", source_artifact_ids=["x"])
        data = json.loads(job.model_dump_json())
        restored = DistillJob.model_validate(data)
        assert restored.job_id == job.job_id
        assert restored.scenario == job.scenario
        assert restored.status == "pending"

    def test_job_status_values(self) -> None:
        from autocontext.openclaw.distill import DistillJob

        for status in ("pending", "running", "completed", "failed"):
            job = DistillJob(scenario="s", status=status)
            assert job.status == status

    def test_job_rejects_bad_status(self) -> None:
        from autocontext.openclaw.distill import DistillJob

        with pytest.raises(ValidationError):
            DistillJob(scenario="s", status="invalid")  # type: ignore[arg-type]

    def test_job_training_config_and_metrics(self) -> None:
        from autocontext.openclaw.distill import DistillJob

        job = DistillJob(
            scenario="grid_ctf",
            training_config={"epochs": 10, "lr": 0.001},
            training_metrics={"loss": 0.05, "accuracy": 0.95},
        )
        assert job.training_config["epochs"] == 10
        assert job.training_metrics["accuracy"] == 0.95


# ---------------------------------------------------------------------------
# TestDistillJobManager
# ---------------------------------------------------------------------------


class TestDistillJobManager:
    def test_create_job(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        job = mgr.create_job("grid_ctf", source_artifact_ids=["a1"])

        assert job.status == "pending"
        assert job.scenario == "grid_ctf"
        assert job.source_artifact_ids == ["a1"]
        # Job file should exist on disk
        job_path = tmp_path / "knowledge" / "_openclaw_distill_jobs" / f"{job.job_id}.json"
        assert job_path.exists()

    def test_get_job(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        created = mgr.create_job("grid_ctf")
        fetched = mgr.get_job(created.job_id)

        assert fetched is not None
        assert fetched.job_id == created.job_id
        assert fetched.scenario == "grid_ctf"

    def test_get_job_not_found(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        assert mgr.get_job("nonexistent") is None

    def test_list_jobs(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        mgr.create_job("grid_ctf")
        mgr.create_job("othello")

        jobs = mgr.list_jobs()
        assert len(jobs) == 2
        scenarios = {j.scenario for j in jobs}
        assert scenarios == {"grid_ctf", "othello"}

    def test_list_jobs_empty(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        assert mgr.list_jobs() == []

    def test_transition_to_running(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        job = mgr.create_job("grid_ctf")
        updated = mgr.transition(job.job_id, "running")

        assert updated is not None
        assert updated.status == "running"
        assert updated.started_at is not None
        # Verify persisted
        refetched = mgr.get_job(job.job_id)
        assert refetched is not None
        assert refetched.status == "running"

    def test_transition_to_completed(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        job = mgr.create_job("grid_ctf")
        mgr.transition(job.job_id, "running")
        updated = mgr.transition(
            job.job_id,
            "completed",
            result_artifact_id="art_123",
            training_metrics={"loss": 0.02},
        )

        assert updated is not None
        assert updated.status == "completed"
        assert updated.completed_at is not None
        assert updated.result_artifact_id == "art_123"
        assert updated.training_metrics["loss"] == 0.02

    def test_transition_to_failed(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        job = mgr.create_job("grid_ctf")
        mgr.transition(job.job_id, "running")
        updated = mgr.transition(job.job_id, "failed", error_message="OOM")

        assert updated is not None
        assert updated.status == "failed"
        assert updated.completed_at is not None
        assert updated.error_message == "OOM"

    def test_transition_invalid_job(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        assert mgr.transition("nonexistent", "running") is None

    def test_transition_invalid_state(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobError, DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        job = mgr.create_job("grid_ctf")
        # pending → completed is not a valid transition
        with pytest.raises(DistillJobError, match="Invalid transition"):
            mgr.transition(job.job_id, "completed")

    def test_completed_requires_result_artifact(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobError, DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        job = mgr.create_job("grid_ctf")
        mgr.transition(job.job_id, "running")

        with pytest.raises(DistillJobError, match="result_artifact_id"):
            mgr.transition(job.job_id, "completed")

    def test_failed_requires_error_message(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobError, DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        job = mgr.create_job("grid_ctf")

        with pytest.raises(DistillJobError, match="error_message"):
            mgr.transition(job.job_id, "failed")

    def test_transition_from_terminal_state(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobError, DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        job = mgr.create_job("grid_ctf")
        mgr.transition(job.job_id, "running")
        mgr.transition(job.job_id, "completed", result_artifact_id="art_123")
        # completed → running is not valid
        with pytest.raises(DistillJobError, match="Invalid transition"):
            mgr.transition(job.job_id, "running")

    def test_active_job_count(self, tmp_path: Path) -> None:
        from autocontext.openclaw.distill import DistillJobManager

        mgr = DistillJobManager(tmp_path / "knowledge")
        mgr.create_job("grid_ctf")
        j2 = mgr.create_job("othello")
        mgr.transition(j2.job_id, "running")
        j3 = mgr.create_job("scenario3")
        mgr.transition(j3.job_id, "running")
        mgr.transition(j3.job_id, "completed", result_artifact_id="art_456")

        assert mgr.active_job_count() == 2  # 1 pending + 1 running


# ---------------------------------------------------------------------------
# TestDistillSidecarProtocol
# ---------------------------------------------------------------------------


class TestDistillSidecarProtocol:
    def test_callable_sidecar_satisfies_protocol(self) -> None:
        from autocontext.openclaw.distill import DistillSidecarProtocol

        class MySidecar:
            def launch(self, job_id: str, scenario: str, config: dict[str, Any]) -> None:
                pass

            def poll(self, job_id: str) -> dict[str, Any]:
                return {"status": "running"}

        sidecar = MySidecar()
        assert isinstance(sidecar, DistillSidecarProtocol)


# ---------------------------------------------------------------------------
# TestUpdatedToolFunctions
# ---------------------------------------------------------------------------


class TestUpdatedToolFunctions:
    def test_trigger_distillation_launches_sidecar(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import trigger_distillation

        ctx = _ctx(tmp_path)
        result = trigger_distillation(ctx, "grid_ctf", source_artifact_ids=["a1"])

        assert result["status"] == "running"
        assert "job_id" in result
        assert result["scenario"] == "grid_ctf"
        assert fake_sidecar.launched == [(str(result["job_id"]), "grid_ctf", {})]
        # Job file should have full schema
        jobs_dir = tmp_path / "knowledge" / "_openclaw_distill_jobs"
        files = list(jobs_dir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert "created_at" in data
        assert "source_artifact_ids" in data

    def test_trigger_distillation_with_training_config(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import trigger_distillation

        ctx = _ctx(tmp_path)
        result = trigger_distillation(
            ctx,
            "grid_ctf",
            training_config={"epochs": 20, "lr": 0.001},
        )

        assert result["status"] == "running"
        assert fake_sidecar.launched[0][2]["epochs"] == 20
        jobs_dir = tmp_path / "knowledge" / "_openclaw_distill_jobs"
        data = json.loads(list(jobs_dir.glob("*.json"))[0].read_text())
        assert data["training_config"]["epochs"] == 20

    def test_trigger_distillation_errors_without_sidecar(self, tmp_path: Path) -> None:
        from autocontext.mcp.tools import trigger_distillation

        ctx = _ctx(tmp_path)
        result = trigger_distillation(ctx, "grid_ctf")

        assert "error" in result
        assert result["status"] == "failed"

    def test_distill_status_returns_full_jobs(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import distill_status, trigger_distillation

        ctx = _ctx(tmp_path)
        trigger_distillation(ctx, "grid_ctf")
        trigger_distillation(ctx, "othello")

        status = distill_status(ctx)
        assert status["active_jobs"] == 2
        assert len(status["jobs"]) == 2
        # Each job should have full schema
        for job in status["jobs"]:
            assert "job_id" in job
            assert "created_at" in job
            assert "status" in job

    def test_distill_status_filters_by_scenario(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import distill_status, trigger_distillation

        ctx = _ctx(tmp_path)
        trigger_distillation(ctx, "grid_ctf")
        trigger_distillation(ctx, "othello")

        status = distill_status(ctx, scenario="grid_ctf")
        assert len(status["jobs"]) == 1
        assert status["jobs"][0]["scenario"] == "grid_ctf"

    def test_distill_status_polls_sidecar_updates(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import distill_status, trigger_distillation

        ctx = _ctx(tmp_path)
        result = trigger_distillation(ctx, "grid_ctf")
        job_id = str(result["job_id"])
        fake_sidecar.poll_results[job_id] = {
            "status": "completed",
            "result_artifact_id": "distilled_123",
            "training_metrics": {"loss": 0.01},
        }

        status = distill_status(ctx)
        assert status["active_jobs"] == 0
        assert status["jobs"][0]["status"] == "completed"
        assert status["jobs"][0]["result_artifact_id"] == "distilled_123"


# ---------------------------------------------------------------------------
# TestJobLifecycleIntegration
# ---------------------------------------------------------------------------


class TestJobLifecycleIntegration:
    def test_full_lifecycle_running_to_completed(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import distill_status, trigger_distillation, update_distill_job

        ctx = _ctx(tmp_path)

        # 1. Trigger
        result = trigger_distillation(ctx, "grid_ctf", source_artifact_ids=["a1"])
        job_id = str(result["job_id"])

        # 2. Check running
        status = distill_status(ctx)
        assert status["active_jobs"] == 1

        # 3. Complete with artifact
        updated = update_distill_job(
            ctx,
            job_id,
            "completed",
            result_artifact_id="distilled_model_001",
            training_metrics={"final_loss": 0.01},
        )
        assert updated["status"] == "completed"
        assert updated["result_artifact_id"] == "distilled_model_001"

        # 4. Status should show 0 active
        status = distill_status(ctx)
        assert status["active_jobs"] == 0

    def test_full_lifecycle_running_to_failed(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import distill_status, trigger_distillation, update_distill_job

        ctx = _ctx(tmp_path)

        result = trigger_distillation(ctx, "grid_ctf")
        job_id = str(result["job_id"])

        updated = update_distill_job(ctx, job_id, "failed", error_message="CUDA OOM")

        assert updated["status"] == "failed"
        assert updated["error_message"] == "CUDA OOM"

        status = distill_status(ctx)
        assert status["active_jobs"] == 0

    def test_get_distill_job_endpoint(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import get_distill_job, trigger_distillation

        ctx = _ctx(tmp_path)
        result = trigger_distillation(ctx, "grid_ctf")
        job_id = str(result["job_id"])

        job = get_distill_job(ctx, job_id)
        assert job["job_id"] == job_id
        assert job["scenario"] == "grid_ctf"
        assert job["status"] == "running"

    def test_get_distill_job_not_found(self, tmp_path: Path) -> None:
        from autocontext.mcp.tools import get_distill_job

        ctx = _ctx(tmp_path)
        result = get_distill_job(ctx, "nonexistent")
        assert "error" in result

    def test_update_distill_job_invalid_transition(self, tmp_path: Path, fake_sidecar: _FakeSidecar) -> None:
        from autocontext.mcp.tools import trigger_distillation, update_distill_job

        ctx = _ctx(tmp_path)
        result = trigger_distillation(ctx, "grid_ctf")
        job_id = str(result["job_id"])

        # running → completed without artifact is invalid
        updated = update_distill_job(ctx, job_id, "completed")
        assert "error" in updated


# ---------------------------------------------------------------------------
# TestMCPServerWrappers
# ---------------------------------------------------------------------------


_has_mcp = True
try:
    from mcp.server.fastmcp import FastMCP  # noqa: F401
except ImportError:
    _has_mcp = False


@pytest.mark.skipif(not _has_mcp, reason="mcp package not installed")
class TestMCPServerWrappers:
    def test_mts_trigger_distillation_exists(self) -> None:
        """Verify the MCP wrapper for trigger_distillation is registered."""
        from autocontext.mcp import server

        assert hasattr(server, "mts_trigger_distillation")

    def test_mts_update_distill_job_exists(self) -> None:
        """Verify the MCP wrapper for update_distill_job is registered."""
        from autocontext.mcp import server

        assert hasattr(server, "mts_update_distill_job")

    def test_mts_get_distill_job_exists(self) -> None:
        """Verify the MCP wrapper for get_distill_job is registered."""
        from autocontext.mcp import server

        assert hasattr(server, "mts_get_distill_job")


# ---------------------------------------------------------------------------
# TestRESTEndpoints
# ---------------------------------------------------------------------------


class TestRESTEndpoints:
    def test_update_distill_job_endpoint_exists(self) -> None:
        """Verify the REST endpoint for updating distill jobs is registered."""
        from autocontext.server.openclaw_api import router

        paths = [r.path for r in router.routes]  # type: ignore[union-attr]
        assert "/api/openclaw/distill/{job_id}" in paths

    def test_get_distill_job_endpoint_exists(self) -> None:
        """Verify the REST endpoint for fetching a single distill job is registered."""
        from autocontext.server.openclaw_api import router

        paths = [r.path for r in router.routes]  # type: ignore[union-attr]
        assert "/api/openclaw/distill/{job_id}" in paths
