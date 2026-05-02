"""Tests for AC-382: Backport judge, improve, repl, queue CLI commands to Python.

These tests verify that the Python CLI exposes the 4 commands that
originated in the TS package.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from autocontext.cli import app

runner = CliRunner()
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


class _FakeProvider:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = outputs
        self.calls: list[dict[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str, model: str) -> SimpleNamespace:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model": model,
            }
        )
        return SimpleNamespace(text=self._outputs.pop(0))

    def default_model(self) -> str:
        return "fake-model"


class TestJudgeCommand:
    def test_judge_help(self) -> None:
        result = runner.invoke(app, ["judge", "--help"])
        assert result.exit_code == 0
        assert "--task-prompt" in result.stdout or "-p" in result.stdout
        assert "--output" in result.stdout or "-o" in result.stdout
        assert "--rubric" in result.stdout or "-r" in result.stdout

    def test_judge_requires_args(self) -> None:
        result = runner.invoke(app, ["judge"])
        assert result.exit_code != 0

    def test_judge_missing_provider_gives_clear_error(self) -> None:
        """Judge without API key should give a clear error, not a stack trace."""
        result = runner.invoke(
            app,
            [
                "judge",
                "--task-prompt",
                "Write a haiku",
                "--output",
                "Test output",
                "--rubric",
                "Score it",
            ],
        )
        # Should fail cleanly (no API key configured)
        assert result.exit_code != 0


class TestImproveCommand:
    def test_improve_help(self) -> None:
        result = runner.invoke(app, ["improve", "--help"])
        assert result.exit_code == 0
        assert "--task-prompt" in result.stdout or "-p" in result.stdout
        assert "--rubric" in result.stdout or "-r" in result.stdout

    def test_improve_requires_args(self) -> None:
        result = runner.invoke(app, ["improve"])
        assert result.exit_code != 0

    def test_improve_generates_initial_output_and_revises(self) -> None:
        fake_provider = _FakeProvider(["initial draft", "revised draft"])
        fake_settings = MagicMock(judge_model="mock-model", judge_provider="anthropic")

        judge_results = [
            SimpleNamespace(
                score=0.2,
                reasoning="Needs work",
                dimension_scores={"quality": 0.2},
                internal_retries=0,
            ),
            SimpleNamespace(
                score=0.95,
                reasoning="Looks good",
                dimension_scores={"quality": 0.95},
                internal_retries=0,
            ),
        ]

        with (
            patch("autocontext.cli.load_settings", return_value=fake_settings),
            patch("autocontext.providers.registry.get_provider", return_value=fake_provider),
            patch("autocontext.execution.task_runner.LLMJudge") as mock_judge_cls,
            patch("autocontext.execution.task_runner.evaluate_evaluator_guardrail", return_value=None),
        ):
            mock_judge_cls.return_value.evaluate.side_effect = judge_results
            result = runner.invoke(
                app,
                [
                    "improve",
                    "--task-prompt",
                    "Write a haiku",
                    "--rubric",
                    "Score quality",
                    "--rounds",
                    "2",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["best_score"] == 0.95
        assert payload["best_output"] == "revised draft"
        assert fake_provider.calls[0]["user_prompt"] == "Write a haiku"
        assert "## Original Output\ninitial draft" in fake_provider.calls[1]["user_prompt"]


class TestQueueCommand:
    def test_queue_help(self) -> None:
        result = runner.invoke(app, ["queue", "--help"])
        stdout = strip_ansi(result.stdout)
        assert result.exit_code == 0
        assert "--spec" in stdout or "-s" in stdout
        assert "--task-prompt" in stdout or "-p" in stdout
        assert "--rounds" in stdout or "-n" in stdout
        assert "--browser-url" in stdout

    def test_queue_requires_spec_or_task_prompt(self) -> None:
        result = runner.invoke(app, ["queue"])
        assert result.exit_code != 0

    def test_queue_uses_task_runner_helper_for_saved_spec(self) -> None:
        settings = MagicMock()
        store = MagicMock()

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._sqlite_from_settings", return_value=store),
            patch("autocontext.execution.task_runner.enqueue_task", return_value="task-123") as mock_enqueue,
        ):
            result = runner.invoke(app, ["queue", "--spec", "demo-task", "--priority", "2", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "task_id": "task-123",
            "spec_name": "demo-task",
            "status": "queued",
        }
        mock_enqueue.assert_called_once_with(store=store, spec_name="demo-task", priority=2)

    def test_queue_add_accepts_direct_task_prompt_aliases(self) -> None:
        settings = MagicMock()
        store = MagicMock()

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._sqlite_from_settings", return_value=store),
            patch("autocontext.execution.task_runner.enqueue_task", return_value="task-456") as mock_enqueue,
        ):
            result = runner.invoke(
                app,
                [
                    "queue",
                    "add",
                    "--task-prompt",
                    "Write a 1-line fact about primes",
                    "--rubric",
                    "correct",
                    "--threshold",
                    "0.8",
                    "--rounds",
                    "2",
                    "--provider",
                    "claude-cli",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["task_id"] == "task-456"
        assert payload["status"] == "queued"
        assert payload["spec_name"] == "write_a_1_line_fact_about_primes"
        mock_enqueue.assert_called_once_with(
            store=store,
            spec_name="write_a_1_line_fact_about_primes",
            task_prompt="Write a 1-line fact about primes",
            rubric="correct",
            quality_threshold=0.8,
            max_rounds=2,
            priority=0,
        )

    def test_queue_passes_browser_url_through_to_the_runner(self) -> None:
        settings = MagicMock()
        store = MagicMock()

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._sqlite_from_settings", return_value=store),
            patch("autocontext.execution.task_runner.enqueue_task", return_value="task-789") as mock_enqueue,
        ):
            result = runner.invoke(
                app,
                [
                    "queue",
                    "--spec",
                    "browser-task",
                    "--browser-url",
                    "https://status.example.com",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "task_id": "task-789",
            "spec_name": "browser-task",
            "status": "queued",
        }
        mock_enqueue.assert_called_once_with(
            store=store,
            spec_name="browser-task",
            browser_url="https://status.example.com",
            priority=0,
        )


class TestWorkerCommand:
    def test_worker_help(self) -> None:
        result = runner.invoke(app, ["worker", "--help"])
        stdout = strip_ansi(result.stdout)
        assert result.exit_code == 0
        assert "--poll-interval" in stdout
        assert "--concurrency" in stdout
        assert "--max-empty-polls" in stdout
        assert "--once" in stdout

    def test_worker_once_wires_task_runner_factory(self) -> None:
        settings = MagicMock(judge_model="settings-model", judge_provider="anthropic")
        store = MagicMock()
        provider = MagicMock()
        provider.default_model.return_value = "provider-model"
        runner_mock = MagicMock(tasks_processed=2)
        runner_mock.run_batch.return_value = 2

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._sqlite_from_settings", return_value=store),
            patch("autocontext.providers.registry.get_provider", return_value=provider),
            patch(
                "autocontext.execution.task_runner.create_task_runner_from_settings",
                return_value=runner_mock,
            ) as mock_create_runner,
        ):
            result = runner.invoke(
                app,
                [
                    "worker",
                    "--once",
                    "--poll-interval",
                    "0.25",
                    "--concurrency",
                    "3",
                    "--max-empty-polls",
                    "1",
                    "--model",
                    "override-model",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "status": "stopped",
            "mode": "once",
            "tasks_processed": 2,
            "poll_interval": 0.25,
            "concurrency": 3,
        }
        runner_mock.run_batch.assert_called_once_with(3)
        mock_create_runner.assert_called_once_with(
            settings,
            store=store,
            provider=provider,
            model="override-model",
            poll_interval=0.25,
            max_consecutive_empty=1,
            concurrency=3,
        )

    def test_worker_forces_single_concurrency_for_stateful_provider(self) -> None:
        settings = MagicMock(judge_model="settings-model", judge_provider="pi-rpc")
        store = MagicMock()
        provider = MagicMock()
        provider.default_model.return_value = "provider-model"
        provider.supports_concurrent_requests = False
        runner_mock = MagicMock(tasks_processed=1)
        runner_mock.run_batch.return_value = 1

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._sqlite_from_settings", return_value=store),
            patch("autocontext.providers.registry.get_provider", return_value=provider),
            patch(
                "autocontext.execution.task_runner.create_task_runner_from_settings",
                return_value=runner_mock,
            ) as mock_create_runner,
        ):
            result = runner.invoke(
                app,
                [
                    "worker",
                    "--once",
                    "--concurrency",
                    "4",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["concurrency"] == 1
        runner_mock.run_batch.assert_called_once_with(1)
        mock_create_runner.assert_called_once()
        assert mock_create_runner.call_args.kwargs["concurrency"] == 1
