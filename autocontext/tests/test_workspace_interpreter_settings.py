"""Workspace interpreter opt-in configuration (AC-901)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.config.settings import AppSettings, load_settings
from autocontext.execution.interpreter_workspace import InterpreterWorkspace
from autocontext.execution.task_runner import (
    SimpleAgentTask,
    TaskConfig,
    TaskRunner,
    _workspace_factory_from_settings,
)
from autocontext.providers.base import CompletionResult, LLMProvider
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


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_WORKSPACE_INTERPRETER_ENABLED", "true")
    monkeypatch.setenv("AUTOCONTEXT_WORKSPACE_INTERPRETER_TIMEOUT_SECONDS", "2.5")
    settings = load_settings()
    assert settings.workspace_interpreter_enabled is True
    assert settings.workspace_interpreter_timeout_seconds == 2.5


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
