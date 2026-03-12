"""Tests for AC-206: Template scaffolding CLI command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.custom.registry import load_all_custom_scenarios

runner = CliRunner()


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


class TestNewScenarioScaffold:
    """Test `autoctx new-scenario --template <name> --name <scenario-name>` command."""

    def test_scaffold_creates_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "knowledge" / "_custom_scenarios" / "my-prompt-task"
        with patch("autocontext.cli._get_custom_scenarios_dir", return_value=tmp_path / "knowledge" / "_custom_scenarios"):
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
        with patch("autocontext.cli._get_custom_scenarios_dir", return_value=tmp_path / "knowledge" / "_custom_scenarios"):
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
        with patch("autocontext.cli._get_custom_scenarios_dir", return_value=tmp_path / "knowledge" / "_custom_scenarios"):
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
        with patch("autocontext.cli._get_custom_scenarios_dir", return_value=tmp_path / "knowledge" / "_custom_scenarios"):
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
        with patch("autocontext.cli._get_custom_scenarios_dir", return_value=tmp_path / "knowledge" / "_custom_scenarios"):
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
