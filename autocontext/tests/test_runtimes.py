"""Tests for agent runtimes."""

from __future__ import annotations

import json
import logging
import subprocess
from unittest.mock import patch

from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.runtimes.base import AgentOutput
from autocontext.runtimes.claude_cli import ClaudeCLIConfig, ClaudeCLIRuntime, create_session_runtime
from autocontext.runtimes.direct_api import DirectAPIRuntime

# ---------------------------------------------------------------------------
# AgentOutput
# ---------------------------------------------------------------------------


class TestAgentOutput:
    def test_defaults(self):
        o = AgentOutput(text="hello")
        assert o.text == "hello"
        assert o.cost_usd is None
        assert o.structured is None
        assert o.metadata == {}

    def test_all_fields(self):
        o = AgentOutput(
            text="hi",
            structured={"key": "val"},
            cost_usd=0.05,
            model="sonnet",
            session_id="abc",
            metadata={"turns": 3},
        )
        assert o.cost_usd == 0.05
        assert o.structured["key"] == "val"


# ---------------------------------------------------------------------------
# DirectAPIRuntime
# ---------------------------------------------------------------------------


class _MockProvider(LLMProvider):
    def __init__(self, response: str = "mock output"):
        self._response = response
        self.calls: list[dict] = []

    def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
        self.calls.append({"system": system_prompt, "user": user_prompt, "model": model})
        return CompletionResult(text=self._response, model=model or "mock")

    def default_model(self):
        return "mock"


class TestDirectAPIRuntime:
    def test_generate(self):
        provider = _MockProvider("Generated text")
        runtime = DirectAPIRuntime(provider)
        result = runtime.generate("Write a poem")
        assert result.text == "Generated text"
        assert provider.calls[0]["user"] == "Write a poem"

    def test_generate_with_system(self):
        provider = _MockProvider("output")
        runtime = DirectAPIRuntime(provider)
        runtime.generate("task", system="Be creative")
        assert provider.calls[0]["system"] == "Be creative"

    def test_revise(self):
        provider = _MockProvider("Revised text")
        runtime = DirectAPIRuntime(provider)
        result = runtime.revise("Write a poem", "old output", "needs more imagery")
        assert result.text == "Revised text"
        assert "old output" in provider.calls[0]["user"]
        assert "needs more imagery" in provider.calls[0]["user"]

    def test_model_passthrough(self):
        provider = _MockProvider()
        runtime = DirectAPIRuntime(provider, model="opus")
        runtime.generate("task")
        assert provider.calls[0]["model"] == "opus"

    def test_name(self):
        runtime = DirectAPIRuntime(_MockProvider())
        assert runtime.name == "DirectAPIRuntime"


# ---------------------------------------------------------------------------
# ClaudeCLIConfig
# ---------------------------------------------------------------------------


class TestClaudeCLIConfig:
    def test_defaults(self):
        cfg = ClaudeCLIConfig()
        assert cfg.model == "sonnet"
        assert cfg.fallback_model == "haiku"
        assert cfg.permission_mode == "bypassPermissions"
        assert cfg.session_persistence is False

    def test_custom(self):
        cfg = ClaudeCLIConfig(model="opus", tools="Bash,Read", timeout=60.0)
        assert cfg.model == "opus"
        assert cfg.tools == "Bash,Read"

    def test_retry_defaults_bound_total_runtime(self):
        cfg = ClaudeCLIConfig()
        assert cfg.max_retries <= 3
        assert cfg.max_total_seconds < 30 * 60


# ---------------------------------------------------------------------------
# ClaudeCLIRuntime
# ---------------------------------------------------------------------------


def _mock_claude_json(result: str = "output text", cost: float = 0.05, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": is_error,
            "result": result,
            "total_cost_usd": cost,
            "session_id": "test-session-123",
            "duration_ms": 1500,
            "num_turns": 1,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "modelUsage": {"claude-sonnet-4-20250514": {"inputTokens": 100, "outputTokens": 50}},
        }
    )


class TestClaudeCLIRuntime:
    def test_build_args_defaults(self):
        runtime = ClaudeCLIRuntime(ClaudeCLIConfig())
        runtime._claude_path = "/usr/bin/claude"
        args = runtime._build_args()
        assert "/usr/bin/claude" in args
        assert "-p" in args
        assert "--output-format" in args
        assert "json" in args
        assert "--model" in args
        assert "sonnet" in args
        assert "--permission-mode" in args
        assert "--no-session-persistence" in args

    def test_build_args_with_tools(self):
        # AC-736: --tools is emitted as a single ``--tools=<value>`` token
        # so empty values render unambiguously in ``ps`` listings.
        cfg = ClaudeCLIConfig(tools="Bash,Read")
        runtime = ClaudeCLIRuntime(cfg)
        runtime._claude_path = "claude"
        args = runtime._build_args()
        assert "--tools=Bash,Read" in args
        # Bare --tools (with separate value arg) must NOT appear.
        assert "--tools" not in args

    def test_build_args_no_tools(self):
        # AC-736: empty tools renders as ``--tools=`` rather than the
        # confusing ``--tools  --permission-mode`` double-space pattern.
        cfg = ClaudeCLIConfig(tools="")
        runtime = ClaudeCLIRuntime(cfg)
        runtime._claude_path = "claude"
        args = runtime._build_args()
        assert "--tools=" in args
        assert "--tools" not in args

    def test_build_args_with_session(self):
        cfg = ClaudeCLIConfig(session_id="my-session", session_persistence=True)
        runtime = ClaudeCLIRuntime(cfg)
        runtime._claude_path = "claude"
        args = runtime._build_args()
        assert "--session-id" in args
        assert "my-session" in args
        assert "--no-session-persistence" not in args

    def test_build_args_with_schema(self):
        runtime = ClaudeCLIRuntime()
        runtime._claude_path = "claude"
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        args = runtime._build_args(schema=schema)
        assert "--json-schema" in args

    def test_build_args_with_system_prompt(self):
        runtime = ClaudeCLIRuntime()
        runtime._claude_path = "claude"
        args = runtime._build_args(system="Be helpful")
        assert "--system-prompt" in args
        idx = args.index("--system-prompt")
        assert args[idx + 1] == "Be helpful"

    def test_parse_output_success(self):
        runtime = ClaudeCLIRuntime()
        raw = _mock_claude_json("hello world", cost=0.03)
        output = runtime._parse_output(raw)
        assert output.text == "hello world"
        assert output.cost_usd == 0.03
        assert output.session_id == "test-session-123"
        assert output.model == "claude-sonnet-4-20250514"
        assert output.metadata["num_turns"] == 1

    def test_parse_output_accumulates_cost(self):
        runtime = ClaudeCLIRuntime()
        runtime._parse_output(_mock_claude_json(cost=0.03))
        runtime._parse_output(_mock_claude_json(cost=0.05))
        assert abs(runtime.total_cost - 0.08) < 1e-9

    def test_parse_output_invalid_json(self):
        runtime = ClaudeCLIRuntime()
        output = runtime._parse_output("not json at all")
        assert output.text == "not json at all"

    def test_parse_output_with_structured(self):
        runtime = ClaudeCLIRuntime()
        data = {
            "type": "result",
            "result": "Paris",
            "structured_output": {"answer": "Paris", "confidence": 1.0},
            "total_cost_usd": 0.01,
        }
        output = runtime._parse_output(json.dumps(data))
        assert output.structured == {"answer": "Paris", "confidence": 1.0}

    @patch("autocontext.runtimes.claude_cli._run_with_group_kill")
    def test_generate_calls_subprocess(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_mock_claude_json("generated"),
            stderr="",
        )
        runtime = ClaudeCLIRuntime()
        runtime._claude_path = "/usr/bin/claude"
        result = runtime.generate("Write something")
        assert result.text == "generated"
        mock_run.assert_called_once()
        # `_run_with_group_kill` receives the prompt as a keyword argument.
        assert mock_run.call_args.kwargs["prompt"] == "Write something"

    @patch("autocontext.runtimes.claude_cli._run_with_group_kill")
    def test_revise_includes_feedback(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_mock_claude_json("revised"),
            stderr="",
        )
        runtime = ClaudeCLIRuntime()
        runtime._claude_path = "/usr/bin/claude"
        result = runtime.revise("Write a poem", "old poem", "needs more rhyme")
        assert result.text == "revised"
        prompt_sent = mock_run.call_args.kwargs["prompt"]
        assert "old poem" in prompt_sent
        assert "needs more rhyme" in prompt_sent

    @patch(
        "autocontext.runtimes.claude_cli._run_with_group_kill",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120),
    )
    def test_timeout_handling(self, mock_run):
        runtime = ClaudeCLIRuntime()
        runtime._claude_path = "/usr/bin/claude"
        result = runtime.generate("slow task")
        assert result.text == ""
        assert result.metadata.get("error") == "timeout"

    @patch("autocontext.runtimes.claude_cli.time.sleep")
    @patch("autocontext.runtimes.claude_cli._run_with_group_kill")
    def test_timeout_retries_are_bounded_and_observable(
        self,
        mock_run,
        mock_sleep,
        caplog,
    ):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=5)
        cfg = ClaudeCLIConfig(
            timeout=5.0,
            max_retries=2,
            retry_backoff_seconds=0.25,
            retry_backoff_multiplier=2.0,
            max_total_seconds=30.0,
        )
        runtime = ClaudeCLIRuntime(cfg)
        runtime._claude_path = "/usr/bin/claude"

        with caplog.at_level(logging.WARNING, logger="autocontext.runtimes.claude_cli"):
            result = runtime.generate("slow task")

        assert result.text == ""
        assert result.metadata.get("error") == "timeout"
        assert result.metadata.get("attempts") == 3
        assert result.metadata.get("retry_exhausted") is True
        assert mock_run.call_count == 3
        assert [call.args[0] for call in mock_sleep.call_args_list] == [0.25, 0.5]
        messages = "\n".join(record.getMessage() for record in caplog.records)
        assert "claude-cli retry attempt=1/2 reason=timeout" in messages
        assert "claude-cli retry attempt=2/2 reason=timeout" in messages
        assert "claude CLI timed out after 3 attempt(s)" in messages

    @patch("autocontext.runtimes.claude_cli.time.sleep")
    @patch("autocontext.runtimes.claude_cli._run_with_group_kill")
    def test_timeout_retry_can_recover(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="claude", timeout=5),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=_mock_claude_json("recovered"),
                stderr="",
            ),
        ]
        cfg = ClaudeCLIConfig(timeout=5.0, max_retries=2, retry_backoff_seconds=0.0)
        runtime = ClaudeCLIRuntime(cfg)
        runtime._claude_path = "/usr/bin/claude"

        result = runtime.generate("slow task")

        assert result.text == "recovered"
        assert result.metadata.get("attempts") == 2
        assert result.metadata.get("retry_count") == 1
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(0.0)

    @patch("autocontext.runtimes.claude_cli.time.sleep")
    @patch(
        "autocontext.runtimes.claude_cli._run_with_group_kill",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1),
    )
    def test_timeout_retry_skips_sleep_that_would_exhaust_total_cap(self, mock_run, mock_sleep):
        cfg = ClaudeCLIConfig(
            timeout=1.0,
            max_retries=2,
            retry_backoff_seconds=60.0,
            max_total_seconds=10.0,
        )
        runtime = ClaudeCLIRuntime(cfg)
        runtime._claude_path = "/usr/bin/claude"

        result = runtime.generate("slow task")

        assert result.metadata.get("error") == "timeout"
        assert result.metadata.get("attempts") == 1
        assert result.metadata.get("retry_exhausted") is True
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()

    @patch("autocontext.runtimes.claude_cli._run_with_group_kill", side_effect=FileNotFoundError)
    def test_missing_cli(self, mock_run):
        runtime = ClaudeCLIRuntime()
        runtime._claude_path = "/usr/bin/claude"
        result = runtime.generate("task")
        assert result.metadata.get("error") == "claude_not_found"

    @patch("autocontext.runtimes.claude_cli._run_with_group_kill")
    def test_nonzero_exit_with_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=_mock_claude_json("partial"),
            stderr="warning",
        )
        runtime = ClaudeCLIRuntime()
        runtime._claude_path = "/usr/bin/claude"
        result = runtime.generate("task")
        assert result.text == "partial"  # Still parses output

    @patch("autocontext.runtimes.claude_cli._run_with_group_kill")
    def test_nonzero_exit_no_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="fatal error",
        )
        runtime = ClaudeCLIRuntime()
        runtime._claude_path = "/usr/bin/claude"
        result = runtime.generate("task")
        assert result.text == ""
        assert result.metadata.get("error") == "nonzero_exit"


# ---------------------------------------------------------------------------
# create_session_runtime
# ---------------------------------------------------------------------------


class TestCreateSessionRuntime:
    def test_creates_with_session_id(self):
        runtime = create_session_runtime(model="opus")
        assert runtime._config.session_id is not None
        assert runtime._config.session_persistence is True
        assert runtime._config.model == "opus"

    def test_unique_session_ids(self):
        r1 = create_session_runtime()
        r2 = create_session_runtime()
        assert r1._config.session_id != r2._config.session_id
