"""Workspace interpreter opt-in configuration (AC-901)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from autocontext.config.settings import AppSettings, load_settings
from autocontext.execution.interpreter_workspace import InterpreterWorkspace
from autocontext.execution.research_workspace import ResearchWorkspace
from autocontext.execution.task_runner import (
    SimpleAgentTask,
    TaskConfig,
    TaskRunner,
    _workspace_factory_from_settings,
)
from autocontext.execution.task_runner_workspaces import evaluate_workspace_candidate
from autocontext.harness.repl.types import ReplResult
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.scenarios.agent_task import AgentTaskResult
from autocontext.storage.sqlite_store import SQLiteStore


class _Provider(LLMProvider):
    def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
        return CompletionResult(text="output", model=model or "mock")

    def default_model(self):
        return "mock-v1"


def test_defaults_are_off() -> None:
    settings = AppSettings()
    assert settings.workspace_interpreter_enabled is False
    assert settings.workspace_interpreter_timeout_seconds == 10.0
    assert settings.workspace_interpreter_backend == "interpreter"
    assert settings.workspace_interpreter_execute_candidates is False
    assert settings.workspace_interpreter_capabilities_approved is False


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_WORKSPACE_INTERPRETER_ENABLED", "true")
    monkeypatch.setenv("AUTOCONTEXT_WORKSPACE_INTERPRETER_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("AUTOCONTEXT_WORKSPACE_INTERPRETER_BACKEND", "docker")
    monkeypatch.setenv("AUTOCONTEXT_WORKSPACE_INTERPRETER_ALLOWED_IMPORTS", '["statistics"]')
    monkeypatch.setenv("AUTOCONTEXT_WORKSPACE_INTERPRETER_ALLOWED_COMMANDS", '["/usr/local/bin/python"]')
    settings = load_settings()
    assert settings.workspace_interpreter_enabled is True
    assert settings.workspace_interpreter_timeout_seconds == 2.5
    assert settings.workspace_interpreter_backend == "docker"
    assert settings.workspace_interpreter_allowed_imports == ("statistics",)
    assert settings.workspace_interpreter_allowed_commands == ("/usr/local/bin/python",)


def test_factory_helper_respects_flag_and_timeout() -> None:
    factory = _workspace_factory_from_settings(
        AppSettings(workspace_interpreter_enabled=True, workspace_interpreter_timeout_seconds=3.0)
    )
    assert factory is not None
    ws = factory()
    assert isinstance(ws, InterpreterWorkspace)
    assert ws._worker._timeout == 3.0  # noqa: SLF001
    ws.close()

    assert _workspace_factory_from_settings(AppSettings()) is None
    assert _workspace_factory_from_settings(None) is None


def test_capable_execution_requires_docker_and_explicit_approval() -> None:
    with pytest.raises(ValueError, match="requires the docker"):
        _workspace_factory_from_settings(
            AppSettings(
                workspace_interpreter_enabled=True,
                workspace_interpreter_execute_candidates=True,
            )
        )
    with pytest.raises(ValueError, match="explicit operator approval"):
        _workspace_factory_from_settings(
            AppSettings(
                workspace_interpreter_enabled=True,
                workspace_interpreter_backend="docker",
                workspace_interpreter_execute_candidates=True,
            )
        )


def test_multi_generation_passes_workspace_factory_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from autocontext.execution import task_runner as task_runner_module

    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(Path(__file__).parent.parent / "migrations")

    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_with_state(self, generations: int) -> tuple[None, None]:
            raise RuntimeError("stop before persistence")

    monkeypatch.setattr(task_runner_module, "AgentTaskEvolutionRunner", FakeRunner)

    provider = _Provider()
    runner = TaskRunner(
        store=store,
        provider=provider,
        settings=AppSettings(workspace_interpreter_enabled=True),
    )
    agent_task = SimpleAgentTask(task_prompt="Do the thing", rubric="Be accurate", provider=provider)

    with pytest.raises(RuntimeError, match="stop before persistence"):
        runner._run_task_multi_generation("task-1", agent_task, "spec-1", "initial", TaskConfig())

    assert callable(captured["workspace_factory"])

    captured.clear()
    disabled_runner = TaskRunner(store=store, provider=provider, settings=AppSettings())
    with pytest.raises(RuntimeError, match="stop before persistence"):
        disabled_runner._run_task_multi_generation("task-2", agent_task, "spec-2", "initial", TaskConfig())
    assert captured["workspace_factory"] is None


def test_multi_generation_passes_live_workspace_evaluator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from autocontext.execution import task_runner as task_runner_module

    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(Path(__file__).parent.parent / "migrations")
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_with_state(self, generations: int) -> tuple[None, None]:
            raise RuntimeError("stop before persistence")

    monkeypatch.setattr(task_runner_module, "AgentTaskEvolutionRunner", FakeRunner)

    def workspace_factory() -> InterpreterWorkspace:
        return InterpreterWorkspace(timeout_seconds=1.0)

    def workspace_evaluate(output: str, generation: int, workspace: InterpreterWorkspace) -> None:
        del output, generation, workspace

    provider = _Provider()
    runner = TaskRunner(
        store=store,
        provider=provider,
        workspace_factory=workspace_factory,
        workspace_evaluate_fn=workspace_evaluate,
    )
    agent_task = SimpleAgentTask(task_prompt="Do the thing", rubric="Be accurate", provider=provider)

    with pytest.raises(RuntimeError, match="stop before persistence"):
        runner._run_task_multi_generation("task-1", agent_task, "spec-1", "initial", TaskConfig())

    assert captured["workspace_factory"] is workspace_factory
    assert captured["workspace_evaluate_fn"] is workspace_evaluate


def test_multi_generation_wires_production_docker_candidate_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autocontext.execution import task_runner as task_runner_module

    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(Path(__file__).parent.parent / "migrations")
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_with_state(self, generations: int) -> tuple[None, None]:
            del generations
            raise RuntimeError("captured")

    monkeypatch.setattr(task_runner_module, "AgentTaskEvolutionRunner", FakeRunner)
    provider = _Provider()
    runner = TaskRunner(
        store=store,
        provider=provider,
        settings=AppSettings(
            workspace_interpreter_enabled=True,
            workspace_interpreter_backend="docker",
            workspace_interpreter_execute_candidates=True,
            workspace_interpreter_capabilities_approved=True,
        ),
    )
    agent_task = SimpleAgentTask(task_prompt="Compute the answer", rubric="Be accurate", provider=provider)

    with pytest.raises(RuntimeError, match="captured"):
        runner._run_task_multi_generation("task-docker", agent_task, "spec", "non-code initial", TaskConfig())

    assert captured["initial_output"] == ""
    assert callable(captured["workspace_factory"])
    assert callable(captured["workspace_evaluate_fn"])


def test_workspace_candidate_keeps_code_but_records_observed_output() -> None:
    class FakeWorkspace:
        def run(self, program: str) -> ReplResult:
            assert program == "answer['value'] = 42"
            return ReplResult(stdout="computed", error=None, answer={"value": 42})

    judged: list[str] = []

    def evaluate_output(output: str, state: dict[str, object], **kwargs: object) -> AgentTaskResult:
        del state, kwargs
        judged.append(output)
        return AgentTaskResult(score=0.9, reasoning="correct")

    evaluation, improvement = evaluate_workspace_candidate(
        "answer['value'] = 42",
        FakeWorkspace(),  # type: ignore[arg-type]
        evaluate_output=evaluate_output,
        quality_threshold=0.8,
        reference_context=None,
        required_concepts=None,
        calibration_examples=None,
    )

    assert judged == ['computed\n{"value": 42}']
    assert evaluation.output == "answer['value'] = 42"
    assert improvement.best_output == "answer['value'] = 42"
    assert improvement.metadata["workspace_execution"]["observed_output"] == 'computed\n{"value": 42}'


@pytest.mark.skipif(
    os.environ.get("AUTOCONTEXT_RUN_DOCKER_TESTS") != "1" or shutil.which("docker") is None,
    reason="set AUTOCONTEXT_RUN_DOCKER_TESTS=1 on a Docker-capable CI worker",
)
def test_production_setting_selects_a_live_isolated_workspace() -> None:
    factory = _workspace_factory_from_settings(
        AppSettings(
            workspace_interpreter_enabled=True,
            workspace_interpreter_backend="docker",
            workspace_interpreter_execute_candidates=True,
            workspace_interpreter_capabilities_approved=True,
        )
    )

    assert factory is not None
    workspace = factory()
    assert isinstance(workspace, ResearchWorkspace)
    assert workspace.request.profile == "isolated_sandbox"
    result = workspace.run("answer['value'] = 6 * 7")
    assert result.error is None
    assert result.answer["value"] == 42
    assert workspace.close().outcome == "deleted"
