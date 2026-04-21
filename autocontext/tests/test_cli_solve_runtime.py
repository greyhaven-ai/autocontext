from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.config.settings import AppSettings
from autocontext.knowledge.export import SkillPackage
from autocontext.knowledge.solver import SolveJob

runner = CliRunner()


class _CapturingSolveManager:
    last_settings: AppSettings | None = None

    def __init__(self, settings: AppSettings) -> None:
        type(self).last_settings = settings

    def solve_sync(
        self,
        description: str,
        generations: int = 5,
        family_override: str | None = None,
    ) -> SolveJob:
        del description, generations, family_override
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="Solve result",
            playbook="## Playbook",
            lessons=["Scout lanes"],
            best_strategy={"aggression": 0.6},
            best_score=0.81,
            best_elo=1512.0,
            hints="Protect home base",
        )
        return SolveJob(
            job_id="solve_1234",
            description="Design a strategy",
            scenario_name="grid_ctf",
            status="completed",
            generations=1,
            progress=1,
            result=pkg,
        )


class _FailingSolveManager:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def solve_sync(
        self,
        description: str,
        generations: int = 5,
        family_override: str | None = None,
    ) -> SolveJob:
        del description, generations, family_override
        return SolveJob(
            job_id="solve_fail",
            description="Broken solve",
            status="failed",
            generations=1,
            progress=0,
            error="PiCLIRuntime failed: timeout",
        )


class _FallbackSolveManager:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def solve_sync(
        self,
        description: str,
        generations: int = 5,
        family_override: str | None = None,
    ) -> SolveJob:
        del description, generations, family_override
        pkg = SkillPackage(
            scenario_name="fallback_case",
            display_name="Fallback Case",
            description="Solve result",
            playbook="## Playbook",
            lessons=["Ask the classifier for help"],
            best_strategy={"aggression": 0.4},
            best_score=0.74,
            best_elo=1498.0,
            hints="Use the fallback metadata",
        )
        return SolveJob(
            job_id="solve_fallback",
            description="Fallback solve",
            scenario_name="fallback_case",
            status="completed",
            generations=1,
            progress=1,
            result=pkg,
            llm_classifier_fallback_used=True,
        )


def _settings(tmp_path: Path, **overrides: object) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        agent_provider=str(overrides.get("agent_provider", "pi")),
        architect_provider=str(overrides.get("architect_provider", "")),
        analyst_provider=str(overrides.get("analyst_provider", "")),
        competitor_provider=str(overrides.get("competitor_provider", "")),
        pi_timeout=float(overrides.get("pi_timeout", 300.0)),
        generation_time_budget_seconds=int(overrides.get("generation_time_budget_seconds", 0)),
    )


class TestSolveRuntimeOverrides:
    def test_solve_timeout_override_updates_runtime_settings(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)

        from unittest.mock import patch

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.knowledge.solver.SolveManager", _CapturingSolveManager),
        ):
            result = runner.invoke(
                app,
                [
                    "solve",
                    "--description",
                    "Design a strategy",
                    "--timeout",
                    "600",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        assert _CapturingSolveManager.last_settings is not None
        assert _CapturingSolveManager.last_settings.pi_timeout == 600.0

    def test_solve_generation_time_budget_override_updates_settings(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)

        from unittest.mock import patch

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.knowledge.solver.SolveManager", _CapturingSolveManager),
        ):
            result = runner.invoke(
                app,
                [
                    "solve",
                    "--description",
                    "Design a strategy",
                    "--generation-time-budget",
                    "120",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        assert _CapturingSolveManager.last_settings is not None
        assert _CapturingSolveManager.last_settings.generation_time_budget_seconds == 120

    def test_solve_timeout_error_mentions_timeout_override(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, pi_timeout=600.0)

        from unittest.mock import patch

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.knowledge.solver.SolveManager", _FailingSolveManager),
        ):
            result = runner.invoke(
                app,
                [
                    "solve",
                    "--description",
                    "Broken solve",
                    "--timeout",
                    "600",
                    "--json",
                ],
            )

        assert result.exit_code == 1
        payload = json.loads(result.stderr)
        assert "timed out" in payload["error"].lower()
        assert "--timeout" in payload["error"]
        assert "AUTOCONTEXT_PI_TIMEOUT" in payload["error"]

    def test_solve_json_output_surfaces_classifier_fallback_flag(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)

        from unittest.mock import patch

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.knowledge.solver.SolveManager", _FallbackSolveManager),
        ):
            result = runner.invoke(
                app,
                [
                    "solve",
                    "--description",
                    "Fallback solve",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["llm_classifier_fallback_used"] is True
        assert payload["scenario_name"] == "fallback_case"
