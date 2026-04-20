"""AC-579 — --family CLI override for autoctx solve."""
from __future__ import annotations

import pytest
import typer

from autocontext.cli_solve import _validate_family_override
from autocontext.scenarios.families import list_families


class TestValidateFamilyOverride:
    def test_empty_string_is_accepted(self) -> None:
        # Empty string means "--family not provided"; no raise.
        _validate_family_override("")

    def test_none_is_accepted(self) -> None:
        # None is also treated as "not provided".
        _validate_family_override(None)

    def test_unknown_family_raises_typer_exit(self) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            _validate_family_override("not_a_real_family")
        assert excinfo.value.exit_code == 1

    @pytest.mark.parametrize("family_name", [f.name for f in list_families()])
    def test_all_registered_families_are_accepted(self, family_name: str) -> None:
        _validate_family_override(family_name)


class TestSolveJobFamilyOverride:
    def test_solve_sync_defaults_family_override_to_none(self, tmp_path) -> None:
        from unittest.mock import patch

        from autocontext.config.settings import AppSettings
        from autocontext.knowledge.solver import SolveJob, SolveManager

        settings = AppSettings(knowledge_root=tmp_path / "knowledge")
        manager = SolveManager(settings)

        captured: dict[str, SolveJob] = {}

        def capture_and_return(job: SolveJob) -> None:
            captured["job"] = job

        with patch.object(manager, "_run_job", side_effect=capture_and_return):
            manager.solve_sync(description="x")

        assert captured["job"].family_override is None

    def test_solve_sync_stores_family_override_on_job(self, tmp_path) -> None:
        from unittest.mock import patch

        from autocontext.config.settings import AppSettings
        from autocontext.knowledge.solver import SolveJob, SolveManager

        settings = AppSettings(knowledge_root=tmp_path / "knowledge")
        manager = SolveManager(settings)

        captured: dict[str, SolveJob] = {}

        def capture_and_return(job: SolveJob) -> None:
            captured["job"] = job

        with patch.object(manager, "_run_job", side_effect=capture_and_return):
            manager.solve_sync(description="x", family_override="simulation")

        assert captured["job"].family_override == "simulation"


class TestSolveScenarioBuilderFamilyOverride:
    """Builder.build routes via family_override when provided, else via classifier."""

    def _make_builder(self, tmp_path):
        from autocontext.knowledge.solver import SolveScenarioBuilder

        def stub_llm_fn(system: str, user: str) -> str:
            return ""

        return SolveScenarioBuilder(
            runtime=object(),
            llm_fn=stub_llm_fn,
            model="stub-model",
            knowledge_root=tmp_path / "knowledge",
        )

    def test_build_skips_classifier_when_family_override_provided(self, tmp_path) -> None:
        from unittest.mock import MagicMock, patch

        from autocontext.knowledge import solver as solver_mod

        builder = self._make_builder(tmp_path)

        def explode(_desc: str):
            raise AssertionError("classifier must not be called when family_override is provided")

        fake_scenario = MagicMock()
        fake_scenario.name = "stub_scenario"

        with patch.object(solver_mod, "_resolve_requested_scenario_family", side_effect=explode):
            with patch(
                "autocontext.scenarios.custom.agent_task_creator.AgentTaskCreator.create",
                return_value=fake_scenario,
            ):
                result = builder.build("anything at all", family_override="simulation")

        assert result.family_name == "simulation"

    def test_build_uses_classifier_when_no_override(self, tmp_path) -> None:
        from unittest.mock import MagicMock, patch

        from autocontext.knowledge import solver as solver_mod
        from autocontext.scenarios.families import get_family

        builder = self._make_builder(tmp_path)

        fake_scenario = MagicMock()
        fake_scenario.name = "stub_scenario"

        classifier_mock = MagicMock(return_value=get_family("simulation"))

        with patch.object(solver_mod, "_resolve_requested_scenario_family", classifier_mock):
            with patch(
                "autocontext.scenarios.custom.agent_task_creator.AgentTaskCreator.create",
                return_value=fake_scenario,
            ):
                result = builder.build("please classify me")

        classifier_mock.assert_called_once()
        args, kwargs = classifier_mock.call_args
        assert args == ("please classify me",)
        assert kwargs["llm_fn"]("", "") == ""
        assert result.family_name == "simulation"


class TestRunSolveCommandFamilyOverride:
    """--family flag is threaded from the CLI through to SolveManager.solve_sync."""

    def test_run_solve_command_forwards_family_override(self, tmp_path) -> None:
        from unittest.mock import MagicMock, patch

        from rich.console import Console

        from autocontext.cli_solve import run_solve_command
        from autocontext.config.settings import AppSettings
        from autocontext.knowledge.export import SkillPackage

        settings = AppSettings(knowledge_root=tmp_path / "knowledge")

        fake_result = MagicMock(spec=SkillPackage)
        fake_result.to_dict.return_value = {"stub": True}

        fake_job = MagicMock()
        fake_job.status = "completed"
        fake_job.result = fake_result
        fake_job.job_id = "job_1"
        fake_job.description = "x"
        fake_job.scenario_name = "stub_scn"
        fake_job.generations = 1
        fake_job.progress = 1
        fake_job.error = None

        with patch("autocontext.knowledge.solver.SolveManager") as manager_cls:
            manager_cls.return_value.solve_sync.return_value = fake_job
            run_solve_command(
                description="x",
                gens=1,
                timeout=None,
                generation_time_budget=None,
                output="",
                json_output=True,
                console=Console(quiet=True),
                load_settings_fn=lambda: settings,
                write_json_stdout=lambda _payload: None,
                write_json_stderr=lambda _msg: None,
                family_override="simulation",
            )
            manager_cls.return_value.solve_sync.assert_called_once_with(
                description="x",
                generations=1,
                family_override="simulation",
            )

    def test_run_solve_command_defaults_family_override_to_none(self, tmp_path) -> None:
        from unittest.mock import MagicMock, patch

        from rich.console import Console

        from autocontext.cli_solve import run_solve_command
        from autocontext.config.settings import AppSettings
        from autocontext.knowledge.export import SkillPackage

        settings = AppSettings(knowledge_root=tmp_path / "knowledge")

        fake_result = MagicMock(spec=SkillPackage)
        fake_result.to_dict.return_value = {"stub": True}

        fake_job = MagicMock()
        fake_job.status = "completed"
        fake_job.result = fake_result
        fake_job.job_id = "job_1"
        fake_job.description = "x"
        fake_job.scenario_name = "stub_scn"
        fake_job.generations = 1
        fake_job.progress = 1
        fake_job.error = None

        with patch("autocontext.knowledge.solver.SolveManager") as manager_cls:
            manager_cls.return_value.solve_sync.return_value = fake_job
            run_solve_command(
                description="x",
                gens=1,
                timeout=None,
                generation_time_budget=None,
                output="",
                json_output=True,
                console=Console(quiet=True),
                load_settings_fn=lambda: settings,
                write_json_stdout=lambda _payload: None,
                write_json_stderr=lambda _msg: None,
            )
            manager_cls.return_value.solve_sync.assert_called_once_with(
                description="x",
                generations=1,
                family_override=None,
            )
