"""Tests for AC-317: Codex CLI runtime and subscription-backed provider routing.

Covers: CodexCLIConfig, CodexCLIRuntime, provider_bridge codex routing.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

# ===========================================================================
# CodexCLIConfig
# ===========================================================================


class TestCodexCLIConfig:
    def test_defaults(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIConfig

        config = CodexCLIConfig()
        assert config.model == "o4-mini"
        assert config.approval_mode == "full-auto"
        assert config.timeout == 120.0

    def test_custom(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIConfig

        config = CodexCLIConfig(model="o3", timeout=300.0, workspace="/tmp/work")
        assert config.model == "o3"
        assert config.workspace == "/tmp/work"


# ===========================================================================
# CodexCLIRuntime
# ===========================================================================


class TestCodexCLIRuntime:
    def test_name(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIRuntime

        runtime = CodexCLIRuntime()
        assert runtime.name == "CodexCLIRuntime"

    def test_build_args_basic(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

        config = CodexCLIConfig(model="o4-mini")
        runtime = CodexCLIRuntime(config)
        args = runtime._build_args()

        assert "exec" in args
        assert "--model" in args
        assert "o4-mini" in args
        assert "--full-auto" in args

    def test_build_args_with_schema(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

        config = CodexCLIConfig()
        runtime = CodexCLIRuntime(config)
        schema = {"type": "object", "properties": {"score": {"type": "number"}}}
        args = runtime._build_args(schema=schema)

        assert "--output-schema" in args

    def test_build_args_quiet_mode(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

        config = CodexCLIConfig(quiet=True)
        runtime = CodexCLIRuntime(config)
        args = runtime._build_args()

        assert "--quiet" in args

    def test_parse_jsonl_output(self) -> None:
        """Parse JSONL event stream to extract final message."""
        from autocontext.runtimes.codex_cli import CodexCLIRuntime

        runtime = CodexCLIRuntime()

        events = [
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "item.message", "content": [{"text": "Hello world"}]}),
            json.dumps({"type": "turn.completed"}),
        ]
        raw = "\n".join(events)
        output = runtime._parse_output(raw)

        assert "Hello" in output.text

    def test_parse_plain_text_fallback(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIRuntime

        runtime = CodexCLIRuntime()
        output = runtime._parse_output("Just plain text response")
        assert output.text == "Just plain text response"

    def test_generate_invokes_subprocess(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

        config = CodexCLIConfig()
        runtime = CodexCLIRuntime(config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"type": "item.message", "content": [{"text": "Generated output"}]})
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            runtime.generate("Write a haiku")

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "Write a haiku" in call_args[0][0] or call_args[1].get("input") == "Write a haiku"

    def test_revise_builds_revision_prompt(self) -> None:
        from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

        config = CodexCLIConfig()
        runtime = CodexCLIRuntime(config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Revised output here"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            output = runtime.revise(
                prompt="Write a haiku",
                previous_output="Old output",
                feedback="Needs more nature imagery",
            )

        assert output.text is not None

    def test_timeout_handled(self) -> None:
        import subprocess

        from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

        config = CodexCLIConfig(timeout=0.1)
        runtime = CodexCLIRuntime(config)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=0.1)):
            output = runtime.generate("test")

        assert output.text == ""
        assert output.metadata.get("error") == "timeout"


# ===========================================================================
# Provider bridge routing for codex
# ===========================================================================


class TestCodexProviderRouting:
    def test_codex_is_recognized_provider_type(self) -> None:
        """The provider bridge should recognize 'codex' as a valid type."""
        from autocontext.runtimes.codex_cli import CODEX_PROVIDER_TYPE

        assert CODEX_PROVIDER_TYPE == "codex"

    def test_cli_runtimes_listed(self) -> None:
        """All subscription-backed runtimes should be discoverable."""
        from autocontext.runtimes import list_cli_runtimes

        runtimes = list_cli_runtimes()
        names = {r["name"] for r in runtimes}
        assert "claude-cli" in names
        assert "codex" in names
        assert "pi" in names
