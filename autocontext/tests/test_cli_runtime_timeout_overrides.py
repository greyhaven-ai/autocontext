from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.cli_runtime_overrides import apply_judge_runtime_overrides
from autocontext.config.settings import AppSettings
from autocontext.providers.base import CompletionResult, ProviderError
from autocontext.providers.registry import get_provider

runner = CliRunner()


class _RecordingProvider:
    def __init__(self, text: str = "generated output") -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        **_: object,
    ) -> CompletionResult:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model": model,
            }
        )
        return CompletionResult(text=self._text, model=model)

    def default_model(self) -> str:
        return "recording-model"


class _TimeoutProvider:
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        **_: object,
    ) -> CompletionResult:
        del system_prompt, user_prompt, model
        raise ProviderError("ClaudeCLIRuntime failed: timeout")

    def default_model(self) -> str:
        return "claude-cli"


class _FakeLoopResult:
    def __init__(self) -> None:
        self.best_score = 0.91
        self.best_round = 1
        self.total_rounds = 1
        self.met_threshold = True
        self.best_output = "generated output"


class _FakeJudge:
    def __init__(self, *, provider, model: str, rubric: str, **_: object) -> None:
        self.provider = provider
        self.model = model
        self.rubric = rubric

    def evaluate(self, *, task_prompt: str, agent_output: str, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            score=0.82,
            reasoning=f"judged {task_prompt} -> {agent_output}",
            dimension_scores={"quality": 0.82},
        )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        judge_provider="anthropic",
        judge_model="judge-default",
        claude_model="sonnet",
        claude_timeout=120.0,
    )


class TestJudgeRuntimeTimeoutOverrides:
    def test_judge_applies_timeout_override_to_claude_cli_provider(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        captured: dict[str, AppSettings] = {}

        def _fake_get_provider(current: AppSettings) -> _RecordingProvider:
            captured["settings"] = current
            return _RecordingProvider()

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.providers.registry.get_provider", side_effect=_fake_get_provider),
            patch("autocontext.execution.judge.LLMJudge", _FakeJudge),
        ):
            result = runner.invoke(
                app,
                [
                    "judge",
                    "-p",
                    "Explain entanglement",
                    "-o",
                    "output",
                    "-r",
                    "Score quality 0-1.",
                    "--provider",
                    "claude-cli",
                    "--timeout",
                    "300",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["score"] == 0.82
        assert captured["settings"].judge_provider == "claude-cli"
        assert captured["settings"].claude_timeout == 300.0


class TestImproveRuntimeTimeoutOverrides:
    def test_improve_applies_timeout_override_to_claude_cli_provider(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        captured: dict[str, AppSettings] = {}
        provider = _RecordingProvider()

        def _fake_get_provider(current: AppSettings) -> _RecordingProvider:
            captured["settings"] = current
            return provider

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.providers.registry.get_provider", side_effect=_fake_get_provider),
            patch("autocontext.execution.improvement_loop.ImprovementLoop") as mock_loop,
        ):
            mock_loop.return_value.run.return_value = _FakeLoopResult()
            result = runner.invoke(
                app,
                [
                    "improve",
                    "-p",
                    "Draft a trial design",
                    "-r",
                    "Score rigor 0-1.",
                    "--provider",
                    "claude-cli",
                    "--timeout",
                    "300",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["best_score"] == 0.91
        assert captured["settings"].judge_provider == "claude-cli"
        assert captured["settings"].claude_timeout == 300.0
        assert provider.calls[0]["user_prompt"] == "Draft a trial design"

    def test_improve_timeout_error_mentions_timeout_override(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.providers.registry.get_provider", return_value=_TimeoutProvider()),
        ):
            result = runner.invoke(
                app,
                [
                    "improve",
                    "-p",
                    "List 5 peer-reviewed studies with DOIs",
                    "-r",
                    "Score factual_accuracy 0-1.",
                    "--provider",
                    "claude-cli",
                    "--json",
                ],
            )

        assert result.exit_code == 1
        payload = json.loads(result.stderr)
        assert "timed out" in payload["error"].lower()
        assert "--timeout" in payload["error"]
        assert "AUTOCONTEXT_CLAUDE_TIMEOUT" in payload["error"]


class TestPiRpcRuntimeTimeoutOverrides:
    def test_pi_rpc_provider_uses_runtime_timeout_override(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path).model_copy(update={"judge_provider": "pi-rpc"})
        overridden = apply_judge_runtime_overrides(settings, timeout=300.0)

        provider = get_provider(overridden)

        assert provider._runtime._config.timeout == 300.0  # type: ignore[attr-defined]
