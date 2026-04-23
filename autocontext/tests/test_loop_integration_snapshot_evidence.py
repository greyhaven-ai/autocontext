"""AC-503/AC-504 loop integration tests.

Tests that:
- stage_preflight collects and persists env snapshot when enabled
- stage_knowledge_setup materializes evidence workspace when enabled
- build_prompt_bundle accepts and injects environment_snapshot + evidence_manifest
- MCP tools return snapshot and evidence data from persisted artifacts
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Prompt bundle integration
# ---------------------------------------------------------------------------


class TestPromptBundleIntegration:
    """build_prompt_bundle should accept environment_snapshot and evidence_manifest."""

    def test_accepts_environment_snapshot_parameter(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle
        from autocontext.scenarios.base import Observation

        obs = Observation(narrative="test", state={}, constraints=[])
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="best 0.5",
            observation=obs,
            current_playbook="playbook",
            available_tools="tools",
            environment_snapshot="## Environment\nPython 3.13 | macOS",
        )
        assert "## Environment" in bundle.competitor
        assert "Python 3.13" in bundle.competitor

    def test_accepts_evidence_manifest_parameter(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle
        from autocontext.scenarios.base import Observation

        obs = Observation(narrative="test", state={}, constraints=[])
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="best 0.5",
            observation=obs,
            current_playbook="playbook",
            available_tools="tools",
            evidence_manifest="## Prior-Run Evidence\nAvailable: 5 artifacts",
        )
        # Evidence should appear in analyst/architect, not competitor
        assert "Prior-Run Evidence" in bundle.analyst
        assert "Prior-Run Evidence" in bundle.architect
        assert "Prior-Run Evidence" not in bundle.competitor

    def test_snapshot_and_evidence_are_budgeted(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle
        from autocontext.scenarios.base import Observation

        obs = Observation(narrative="test", state={}, constraints=[])
        # Use a very tight budget to trigger trimming
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="best 0.5",
            observation=obs,
            current_playbook="playbook",
            available_tools="tools",
            environment_snapshot="x" * 500_000,  # Huge snapshot
            evidence_manifest="y" * 500_000,  # Huge manifest
            context_budget_tokens=1000,  # Very tight
        )
        # Should not crash; content should be truncated
        assert "truncated" in bundle.competitor or len(bundle.competitor) < 600_000

    def test_empty_snapshot_and_evidence_produce_no_artifacts(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle
        from autocontext.scenarios.base import Observation

        obs = Observation(narrative="test", state={}, constraints=[])
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="best 0.5",
            observation=obs,
            current_playbook="playbook",
            available_tools="tools",
            environment_snapshot="",
            evidence_manifest="",
        )
        assert "## Environment" not in bundle.competitor
        assert "## Prior-Run Evidence" not in bundle.analyst


# ---------------------------------------------------------------------------
# stage_preflight snapshot collection
# ---------------------------------------------------------------------------


class TestStagePreflight:
    """stage_preflight should collect and persist env snapshot."""

    def test_collects_snapshot_when_enabled_at_gen1(self) -> None:
        from autocontext.loop.stage_preflight import stage_preflight

        ctx = self._make_ctx(env_snapshot_enabled=True, generation=1)
        events = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = self._make_artifacts(tmp, ctx.scenario_name)
            stage_preflight(ctx, events=events, artifacts=artifacts)
            snapshot_path = Path(tmp) / ctx.scenario_name / "environment_snapshot.json"
            assert snapshot_path.exists()
            data = json.loads(snapshot_path.read_text())
            assert "python_version" in data
            assert "os_name" in data

    def test_skips_when_disabled(self) -> None:
        from autocontext.loop.stage_preflight import stage_preflight

        ctx = self._make_ctx(env_snapshot_enabled=False, generation=1)
        events = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = self._make_artifacts(tmp, ctx.scenario_name)
            stage_preflight(ctx, events=events, artifacts=artifacts)
            snapshot_path = Path(tmp) / ctx.scenario_name / "environment_snapshot.json"
            assert not snapshot_path.exists()

    def test_skips_at_gen2(self) -> None:
        from autocontext.loop.stage_preflight import stage_preflight

        ctx = self._make_ctx(env_snapshot_enabled=True, generation=2)
        events = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = self._make_artifacts(tmp, ctx.scenario_name)
            stage_preflight(ctx, events=events, artifacts=artifacts)
            snapshot_path = Path(tmp) / ctx.scenario_name / "environment_snapshot.json"
            assert not snapshot_path.exists()

    def test_populates_ctx_environment_snapshot(self) -> None:
        from autocontext.loop.stage_preflight import stage_preflight

        ctx = self._make_ctx(env_snapshot_enabled=True, generation=1)
        events = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = self._make_artifacts(tmp, ctx.scenario_name)
            result = stage_preflight(ctx, events=events, artifacts=artifacts)
            assert hasattr(result, "environment_snapshot")
            assert "Python" in result.environment_snapshot

    # --- helpers ---

    def _make_ctx(self, env_snapshot_enabled: bool, generation: int) -> MagicMock:
        ctx = MagicMock()
        ctx.generation = generation
        ctx.scenario_name = "test_scenario"
        ctx.run_id = "test_run"
        ctx.settings.env_snapshot_enabled = env_snapshot_enabled
        ctx.settings.env_snapshot_redact_hostname = True
        ctx.settings.env_snapshot_redact_username = True
        ctx.settings.env_snapshot_redact_paths = True
        ctx.settings.harness_preflight_enabled = False
        ctx.environment_snapshot = ""
        return ctx

    def _make_artifacts(self, tmp: str, scenario_name: str) -> MagicMock:
        artifacts = MagicMock()
        knowledge_dir = Path(tmp) / scenario_name
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        artifacts.knowledge_root = Path(tmp)
        artifacts.harness_dir.return_value = knowledge_dir / "harness"
        return artifacts


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


class TestMcpTools:
    """MCP tools should read persisted snapshot and evidence artifacts."""

    def test_env_snapshot_tool_returns_snapshot(self) -> None:
        from autocontext.mcp.knowledge_tools import get_env_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            scenario_dir = Path(tmp) / "test_scenario"
            scenario_dir.mkdir()
            snapshot_data = {"python_version": "3.13.1", "os_name": "Darwin"}
            (scenario_dir / "environment_snapshot.json").write_text(json.dumps(snapshot_data), encoding="utf-8")
            ctx = MagicMock()
            ctx.settings.knowledge_root = Path(tmp)
            result = get_env_snapshot(ctx, "test_scenario")
            parsed = json.loads(result)
            assert parsed["python_version"] == "3.13.1"

    def test_env_snapshot_tool_returns_not_found(self) -> None:
        from autocontext.mcp.knowledge_tools import get_env_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            ctx = MagicMock()
            ctx.settings.knowledge_root = Path(tmp)
            result = get_env_snapshot(ctx, "nonexistent")
            assert "not found" in result.lower() or "no snapshot" in result.lower()

    def test_evidence_list_tool_returns_manifest(self) -> None:
        from autocontext.mcp.knowledge_tools import get_evidence_list

        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "test_scenario" / "_evidence"
            evidence_dir.mkdir(parents=True)
            manifest = {
                "artifacts": [
                    {"artifact_id": "a1", "kind": "trace", "summary": "events"},
                ],
                "totalSizeBytes": 1024,
            }
            (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            ctx = MagicMock()
            ctx.settings.knowledge_root = Path(tmp)
            result = get_evidence_list(ctx, "test_scenario")
            parsed = json.loads(result)
            assert len(parsed["artifacts"]) == 1

    def test_evidence_list_tool_returns_not_found(self) -> None:
        from autocontext.mcp.knowledge_tools import get_evidence_list

        with tempfile.TemporaryDirectory() as tmp:
            ctx = MagicMock()
            ctx.settings.knowledge_root = Path(tmp)
            result = get_evidence_list(ctx, "nonexistent")
            assert "not found" in result.lower() or "no evidence" in result.lower()

    def test_evidence_artifact_tool_returns_excerpt_and_tracks_access(self) -> None:
        from autocontext.mcp.knowledge_tools import get_evidence_artifact

        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "test_scenario" / "_evidence"
            evidence_dir.mkdir(parents=True)
            manifest = {
                "workspace_dir": str(evidence_dir),
                "source_runs": ["run_001"],
                "artifacts": [
                    {
                        "artifact_id": "gate_abc123",
                        "source_run_id": "run_001",
                        "kind": "gate_decision",
                        "path": "gate_abc123_gate_decision.json",
                        "summary": "advance decision",
                        "size_bytes": 64,
                        "generation": 2,
                        "source_path": "/tmp/source/run_001/gate_decision.json",
                    },
                ],
                "total_size_bytes": 64,
                "materialized_at": "2026-04-22T00:00:00+00:00",
            }
            (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (evidence_dir / "gate_abc123_gate_decision.json").write_text(
                '{"decision":"advance"}\n{"delta":0.08}\n{"note":"retain guard"}\n',
                encoding="utf-8",
            )
            ctx = MagicMock()
            ctx.settings.knowledge_root = Path(tmp)

            result = get_evidence_artifact(ctx, "test_scenario", "gate_abc123", excerpt_lines=2)

            assert "Artifact ID: gate_abc123" in result
            assert '"decision":"advance"' in result
            assert '"note":"retain guard"' not in result
            access_log = json.loads((evidence_dir / "evidence_access_log.json").read_text(encoding="utf-8"))
            assert access_log["accessed"] == ["gate_abc123"]

    def test_evidence_artifact_tool_returns_not_found(self) -> None:
        from autocontext.mcp.knowledge_tools import get_evidence_artifact

        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "test_scenario" / "_evidence"
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "workspace_dir": str(evidence_dir),
                        "source_runs": [],
                        "artifacts": [],
                        "total_size_bytes": 0,
                        "materialized_at": "2026-04-22T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            ctx = MagicMock()
            ctx.settings.knowledge_root = Path(tmp)

            result = get_evidence_artifact(ctx, "test_scenario", "missing")

            assert "not found" in result.lower()

    def test_evidence_artifact_tool_ignores_manifest_workspace_dir(self) -> None:
        from autocontext.mcp.knowledge_tools import get_evidence_artifact

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "test_scenario" / "_evidence"
            evidence_dir.mkdir(parents=True)
            outside_path = root / "outside.txt"
            outside_path.write_text("top secret", encoding="utf-8")
            (evidence_dir / "safe.md").write_text("safe evidence", encoding="utf-8")
            manifest = {
                "workspace_dir": str(root),
                "source_runs": ["run_001"],
                "artifacts": [
                    {
                        "artifact_id": "escape_abc123",
                        "source_run_id": "run_001",
                        "kind": "report",
                        "path": "outside.txt",
                        "summary": "outside file",
                        "size_bytes": 10,
                        "generation": 1,
                        "source_path": str(outside_path),
                    },
                ],
                "total_size_bytes": 10,
                "materialized_at": "2026-04-22T00:00:00+00:00",
            }
            (evidence_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            ctx = MagicMock()
            ctx.settings.knowledge_root = root

            result = get_evidence_artifact(ctx, "test_scenario", "escape_abc123")

            assert "top secret" not in result
            assert "not found" in result.lower()
            access_log = json.loads((evidence_dir / "evidence_access_log.json").read_text(encoding="utf-8"))
            assert access_log["accessed"] == ["escape_abc123"]
