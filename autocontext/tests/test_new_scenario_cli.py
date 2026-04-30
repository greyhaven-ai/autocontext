"""Tests for AC-206: Template scaffolding CLI command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.config.settings import AppSettings
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.custom.registry import load_all_custom_scenarios

runner = CliRunner()


def _custom_dir(tmp_path: Path) -> Path:
    return tmp_path / "knowledge" / "_custom_scenarios"


class TestNewScenarioList:
    """Test `autoctx new-scenario --list` command."""

    def test_list_shows_templates(self) -> None:
        result = runner.invoke(app, ["new-scenario", "--list"])
        assert result.exit_code == 0
        assert "prompt-optimization" in result.stdout
        assert "rag-accuracy" in result.stdout
        assert "content-generation" in result.stdout

    def test_list_shows_descriptions(self) -> None:
        result = runner.invoke(app, ["new-scenario", "--list"])
        assert result.exit_code == 0
        # Each template should show its description
        assert "Optimize" in result.stdout or "optimize" in result.stdout

    def test_list_families_shows_registered_pipelines(self) -> None:
        result = runner.invoke(app, ["new-scenario", "--list-families"])

        assert result.exit_code == 0
        assert "schema_evolution" in result.stdout
        assert "operator_loop" in result.stdout
        assert "tool_fragility" in result.stdout


class TestNewScenarioScaffold:
    """Test `autoctx new-scenario --template <name> --name <scenario-name>` command."""

    def test_family_pipeline_requires_description(self) -> None:
        result = runner.invoke(
            app,
            ["new-scenario", "--family", "schema_evolution", "--name", "api-drift", "--non-interactive"],
        )

        assert result.exit_code != 0
        assert "--description" in result.stdout

    def test_family_pipeline_invokes_registered_creator(self, tmp_path: Path) -> None:
        calls: list[dict[str, object]] = []

        def _fake_create_family_scenario(
            *,
            family: str,
            name: str,
            description: str,
            settings: AppSettings,
        ) -> object:
            calls.append(
                {
                    "family": family,
                    "name": name,
                    "description": description,
                    "knowledge_root": settings.knowledge_root,
                }
            )
            return object()

        with (
            patch("autocontext.cli.load_settings", return_value=AppSettings(knowledge_root=tmp_path / "knowledge")),
            patch("autocontext.cli_new_scenario._create_family_scenario", side_effect=_fake_create_family_scenario),
        ):
            result = runner.invoke(
                app,
                [
                    "new-scenario",
                    "--family",
                    "schema_evolution",
                    "--name",
                    "api-drift",
                    "--description",
                    "API contracts drift while producers and consumers evolve",
                    "--non-interactive",
                ],
            )

        assert result.exit_code == 0
        assert calls == [
            {
                "family": "schema_evolution",
                "name": "api-drift",
                "description": "API contracts drift while producers and consumers evolve",
                "knowledge_root": tmp_path / "knowledge",
            }
        ]
        assert "created with family pipeline 'schema_evolution'" in result.stdout

    def test_scaffold_creates_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "knowledge" / "_custom_scenarios" / "my-prompt-task"
        with patch("autocontext.cli_new_scenario._get_custom_scenarios_dir", return_value=_custom_dir(tmp_path)):
            result = runner.invoke(
                app,
                ["new-scenario", "--template", "prompt-optimization", "--name", "my-prompt-task", "--non-interactive"],
            )
        assert result.exit_code == 0
        assert target.is_dir()
        assert (target / "spec.yaml").is_file()
        assert (target / "agent_task.py").is_file()
        assert (target / "scenario_type.txt").is_file()

    def test_scaffold_with_judge_model(self, tmp_path: Path) -> None:
        with patch("autocontext.cli_new_scenario._get_custom_scenarios_dir", return_value=_custom_dir(tmp_path)):
            result = runner.invoke(
                app,
                [
                    "new-scenario",
                    "--template", "rag-accuracy",
                    "--name", "my-rag",
                    "--judge-model", "test-judge-model",
                    "--non-interactive",
                ],
            )
        assert result.exit_code == 0
        target = tmp_path / "knowledge" / "_custom_scenarios" / "my-rag"
        assert target.is_dir()
        assert "test-judge-model" in (target / "agent_task.py").read_text(encoding="utf-8")

    def test_scaffold_missing_template(self, tmp_path: Path) -> None:
        with patch("autocontext.cli_new_scenario._get_custom_scenarios_dir", return_value=_custom_dir(tmp_path)):
            result = runner.invoke(
                app,
                ["new-scenario", "--template", "nonexistent", "--name", "test", "--non-interactive"],
            )
        assert result.exit_code != 0

    def test_scaffold_missing_name(self) -> None:
        result = runner.invoke(
            app,
            ["new-scenario", "--template", "prompt-optimization"],
        )
        # Should fail without --name when not listing
        assert result.exit_code != 0

    def test_scaffold_registers_scenario(self, tmp_path: Path) -> None:
        """After scaffolding, the scenario should be registered."""
        with patch("autocontext.cli_new_scenario._get_custom_scenarios_dir", return_value=_custom_dir(tmp_path)):
            result = runner.invoke(
                app,
                [
                    "new-scenario",
                    "--template", "content-generation",
                    "--name", "my-blog-task",
                    "--non-interactive",
                ],
            )
        try:
            assert result.exit_code == 0
            assert "my-blog-task" in SCENARIO_REGISTRY
            loaded = load_all_custom_scenarios(tmp_path / "knowledge")
            assert "my-blog-task" in loaded
        finally:
            SCENARIO_REGISTRY.pop("my-blog-task", None)


class TestNewScenarioNonInteractive:
    """Test the --non-interactive flag."""

    def test_non_interactive_uses_defaults(self, tmp_path: Path) -> None:
        with patch("autocontext.cli_new_scenario._get_custom_scenarios_dir", return_value=_custom_dir(tmp_path)):
            result = runner.invoke(
                app,
                [
                    "new-scenario",
                    "--template", "prompt-optimization",
                    "--name", "auto-test",
                    "--non-interactive",
                ],
            )
        assert result.exit_code == 0
        target = tmp_path / "knowledge" / "_custom_scenarios" / "auto-test"
        assert target.is_dir()
