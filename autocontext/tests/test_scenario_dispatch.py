"""Tests for AC-297 + AC-299: CLI scenario dispatch and ScenarioInfo literals."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.config.settings import AppSettings
from autocontext.loop.generation_runner import RunSummary
from autocontext.scenarios.agent_task import AgentTaskInterface
from autocontext.scenarios.negotiation import NegotiationInterface

runner = CliRunner()


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        agent_provider="deterministic",
        judge_provider="anthropic",
        anthropic_api_key="test-key",
    )


class TestCliDispatch:
    def test_agent_task_family_uses_direct_agent_task_path(self) -> None:
        from autocontext.cli import _is_agent_task

        with (
            patch.dict("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": object}, clear=True),
            patch(
                "autocontext.scenarios.families.detect_family",
                return_value=SimpleNamespace(
                    name="agent_task",
                    interface_class=AgentTaskInterface,
                ),
            ),
        ):
            assert _is_agent_task("mock_task") is True

    def test_negotiation_family_does_not_use_agent_task_path(self) -> None:
        from autocontext.cli import _is_agent_task

        with (
            patch.dict("autocontext.cli.SCENARIO_REGISTRY", {"mock_negotiation": object}, clear=True),
            patch(
                "autocontext.scenarios.families.detect_family",
                return_value=SimpleNamespace(
                    name="negotiation",
                    interface_class=NegotiationInterface,
                ),
            ),
        ):
            assert _is_agent_task("mock_negotiation") is False

    def test_run_routes_negotiation_family_through_generation_runner(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        mock_summary = RunSummary(
            run_id="neg-run-001",
            scenario="consulting_negotiation",
            generations_executed=1,
            best_score=0.72,
            current_elo=1000.0,
        )
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_summary

        with (
            patch.dict("autocontext.cli.SCENARIO_REGISTRY", {"consulting_negotiation": object}, clear=True),
            patch(
                "autocontext.scenarios.families.detect_family",
                return_value=SimpleNamespace(
                    name="negotiation",
                    interface_class=NegotiationInterface,
                ),
            ),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._runner", return_value=mock_runner),
            patch("autocontext.cli._run_agent_task") as mock_run_agent_task,
        ):
            result = runner.invoke(app, ["run", "--scenario", "consulting_negotiation", "--gens", "1"])

        assert result.exit_code == 0, result.output
        mock_runner.run.assert_called_once_with(
            scenario_name="consulting_negotiation",
            generations=1,
            run_id=None,
        )
        mock_run_agent_task.assert_not_called()


class TestScenarioInfoTypes:
    def test_negotiation_accepted(self) -> None:
        from autocontext.openclaw.models import ScenarioInfo

        info = ScenarioInfo(
            name="consulting_negotiation",
            display_name="Consulting Negotiation",
            scenario_type="negotiation",
            description="A negotiation scenario",
        )
        assert info.scenario_type == "negotiation"

    def test_schema_evolution_accepted(self) -> None:
        from autocontext.openclaw.models import ScenarioInfo

        info = ScenarioInfo(
            name="schema_evo",
            display_name="Schema Evolution",
            scenario_type="schema_evolution",
            description="A schema evolution scenario",
        )
        assert info.scenario_type == "schema_evolution"

    def test_tool_fragility_accepted(self) -> None:
        from autocontext.openclaw.models import ScenarioInfo

        info = ScenarioInfo(
            name="api_drift",
            display_name="API Drift",
            scenario_type="tool_fragility",
            description="A tool fragility scenario",
        )
        assert info.scenario_type == "tool_fragility"

    def test_operator_loop_accepted(self) -> None:
        from autocontext.openclaw.models import ScenarioInfo

        info = ScenarioInfo(
            name="ops_escalation",
            display_name="Ops Escalation",
            scenario_type="operator_loop",
            description="An operator loop scenario",
        )
        assert info.scenario_type == "operator_loop"

    def test_coordination_accepted(self) -> None:
        from autocontext.openclaw.models import ScenarioInfo

        info = ScenarioInfo(
            name="multi_agent",
            display_name="Multi-Agent",
            scenario_type="coordination",
            description="A coordination scenario",
        )
        assert info.scenario_type == "coordination"

    def test_all_registered_families_accepted(self) -> None:
        """Every registered family name should be a valid scenario_type."""
        from pydantic import ValidationError

        from autocontext.openclaw.models import ScenarioInfo
        from autocontext.scenarios.families import list_families

        for family in list_families():
            try:
                ScenarioInfo(
                    name=f"test_{family.name}",
                    display_name=f"Test {family.name}",
                    scenario_type=family.name,
                    description=f"Test {family.name} scenario",
                )
            except ValidationError as exc:
                raise AssertionError(
                    f"ScenarioInfo rejected scenario_type='{family.name}' "
                    f"but family '{family.name}' is registered"
                ) from exc
