"""End-to-end wiring tests for ``solve --task-prompt`` (AC-734).

The verbatim build module is unit-tested in test_solve_verbatim_prompt.py.
This module pins the wiring from CLI flag → SolveManager.solve_sync →
build_verbatim_solve_scenario → SCENARIO_REGISTRY without requiring an
LLM provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.config.settings import AppSettings


@pytest.fixture
def isolated_settings(tmp_path: Path) -> AppSettings:
    """Settings rooted in tmp_path so test runs don't share state."""
    return AppSettings(
        knowledge_root=tmp_path / "knowledge",
        db_path=str(tmp_path / "ac.db"),
        agent_provider="deterministic",
    )


class TestSolveManagerVerbatimMode:
    def test_verbatim_task_prompt_does_not_call_llm_designer(
        self,
        isolated_settings,
        monkeypatch,
    ):
        """When verbatim mode is on, the LLM scenario designer must
        not be invoked. The whole point is bypassing that pipeline.
        """
        from autocontext.scenarios.custom import agent_task_designer

        def _explode(*args, **kwargs):
            raise AssertionError("LLM designer must not run in verbatim mode")

        monkeypatch.setattr(
            agent_task_designer,
            "design_validated_agent_task",
            _explode,
        )

        from autocontext.knowledge.solver import SolveManager

        manager = SolveManager(isolated_settings)
        # We monkeypatch the executor so we don't need a real provider —
        # this test only verifies the BUILD step skips the designer.
        from autocontext.knowledge import solver as solver_mod

        class _StubExecutor:
            def __init__(self, *args, **kwargs):  # noqa: D401, ANN001, ARG002
                pass

            def execute(self, *, scenario_name, family_name, generations):  # noqa: ANN001
                from autocontext.knowledge.solver import SolveExecutionSummary

                return SolveExecutionSummary(
                    run_id="test_run",
                    generations_executed=generations,
                    best_score=0.5,
                )

        monkeypatch.setattr(solver_mod, "SolveScenarioExecutor", _StubExecutor)

        # Stub the skill export so we don't need a real artifact store.
        def _stub_export(ctx, scenario_name):  # noqa: ANN001, ARG001
            from autocontext.knowledge.export import SkillPackage

            return SkillPackage(
                scenario_name=scenario_name,
                display_name=scenario_name,
                description="stub",
                playbook="",
                lessons=[],
                best_strategy=None,
                best_score=0.0,
                best_elo=0.0,
                hints="",
            )

        monkeypatch.setattr(solver_mod, "export_skill_package", _stub_export)

        marker = "PROVE-ME-EXACTLY-AS-WRITTEN-12345"
        job = manager.solve_sync(
            description="prove the lemma about subset",
            generations=1,
            verbatim_task_prompt=f"Please produce: {marker}",
        )
        assert job.status == "completed"
        assert job.scenario_name is not None

        # The registered scenario should carry the verbatim text.
        from autocontext.scenarios import SCENARIO_REGISTRY

        cls = SCENARIO_REGISTRY[job.scenario_name]
        instance = cls()
        assert marker in instance.get_task_prompt(instance.initial_state())

    def test_solve_sync_signature_accepts_verbatim_task_prompt(
        self,
        isolated_settings,
    ):
        # Pin the API contract: the kwarg exists and is optional.
        import inspect

        from autocontext.knowledge.solver import SolveManager

        sig = inspect.signature(SolveManager.solve_sync)
        assert "verbatim_task_prompt" in sig.parameters
        assert sig.parameters["verbatim_task_prompt"].default is None


class TestCliVerbatimFlag:
    def test_solve_cli_exposes_task_prompt_flag(self):
        # Pin the CLI surface: --task-prompt is registered. Inspect the
        # Click/Typer command's parameter list directly so we don't depend
        # on terminal width when scanning rendered help.
        from autocontext.cli import app

        # Locate the registered solve command.
        click_app = typer_to_click_main(app)
        solve_cmd = click_app.commands["solve"]
        flag_names = {name for param in solve_cmd.params for name in getattr(param, "opts", [])}
        assert "--task-prompt" in flag_names


def typer_to_click_main(app):
    """Convert a typer.Typer to its underlying click MultiCommand.

    Works across recent typer versions by going through ``typer.main.get_command``.
    """
    import typer.main as typer_main

    return typer_main.get_command(app)
