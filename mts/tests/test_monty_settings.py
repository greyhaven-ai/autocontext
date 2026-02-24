"""Tests for Monty executor settings and GenerationRunner wiring."""
from __future__ import annotations

from unittest.mock import patch

from mts.config.settings import AppSettings, load_settings


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
            "MTS_EXECUTOR_MODE": "monty",
            "MTS_MONTY_MAX_EXECUTION_TIME_SECONDS": "45.0",
            "MTS_MONTY_MAX_EXTERNAL_CALLS": "150",
        }):
            settings = load_settings()
            assert settings.executor_mode == "monty"
            assert settings.monty_max_execution_time_seconds == 45.0
            assert settings.monty_max_external_calls == 150


class TestGenerationRunnerMontyWiring:
    def test_monty_executor_mode_creates_monty_executor(self) -> None:
        """GenerationRunner with executor_mode=monty uses MontyExecutor."""
        from mts.execution.executors.monty import MontyExecutor

        settings = AppSettings(
            agent_provider="deterministic",
            executor_mode="monty",
        )
        from mts.loop.generation_runner import GenerationRunner
        runner = GenerationRunner(settings)
        assert isinstance(runner.executor.executor, MontyExecutor)
        assert runner.remote is None

    def test_local_executor_mode_unchanged(self) -> None:
        """GenerationRunner with executor_mode=local still uses LocalExecutor."""
        from mts.execution.executors.local import LocalExecutor

        settings = AppSettings(agent_provider="deterministic", executor_mode="local")
        from mts.loop.generation_runner import GenerationRunner
        runner = GenerationRunner(settings)
        assert isinstance(runner.executor.executor, LocalExecutor)
        assert runner.remote is None
