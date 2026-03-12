"""Tests for Monty executor settings and GenerationRunner wiring."""
from __future__ import annotations

from unittest.mock import patch

from autocontext.config.settings import AppSettings, load_settings


class TestMontySettings:
    def test_default_executor_mode_is_local(self) -> None:
        settings = AppSettings()
        assert settings.executor_mode == "local"

    def test_monty_executor_mode_accepted(self) -> None:
        settings = AppSettings(executor_mode="monty")
        assert settings.executor_mode == "monty"

    def test_monty_max_execution_time(self) -> None:
        settings = AppSettings(monty_max_execution_time_seconds=60.0)
        assert settings.monty_max_execution_time_seconds == 60.0

    def test_monty_max_execution_time_default(self) -> None:
        settings = AppSettings()
        assert settings.monty_max_execution_time_seconds == 30.0

    def test_monty_max_external_calls(self) -> None:
        settings = AppSettings(monty_max_external_calls=200)
        assert settings.monty_max_external_calls == 200

    def test_monty_max_external_calls_default(self) -> None:
        settings = AppSettings()
        assert settings.monty_max_external_calls == 100

    def test_load_settings_reads_monty_env_vars(self) -> None:
        with patch.dict("os.environ", {
            "AUTOCONTEXT_EXECUTOR_MODE": "monty",
            "AUTOCONTEXT_MONTY_MAX_EXECUTION_TIME_SECONDS": "45.0",
            "AUTOCONTEXT_MONTY_MAX_EXTERNAL_CALLS": "150",
        }):
            settings = load_settings()
            assert settings.executor_mode == "monty"
            assert settings.monty_max_execution_time_seconds == 45.0
            assert settings.monty_max_external_calls == 150


class TestGenerationRunnerMontyWiring:
    def test_monty_executor_mode_creates_monty_executor(self) -> None:
        """GenerationRunner with executor_mode=monty uses MontyExecutor."""
        from autocontext.execution.executors.monty import MontyExecutor

        settings = AppSettings(
            agent_provider="deterministic",
            executor_mode="monty",
        )
        from autocontext.loop.generation_runner import GenerationRunner
        runner = GenerationRunner(settings)
        assert isinstance(runner.executor.executor, MontyExecutor)
        assert runner.remote is None

    def test_local_executor_mode_unchanged(self) -> None:
        """GenerationRunner with executor_mode=local still uses LocalExecutor."""
        from autocontext.execution.executors.local import LocalExecutor

        settings = AppSettings(agent_provider="deterministic", executor_mode="local")
        from autocontext.loop.generation_runner import GenerationRunner
        runner = GenerationRunner(settings)
        assert isinstance(runner.executor.executor, LocalExecutor)
        assert runner.remote is None
