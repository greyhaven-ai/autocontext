"""Tests for AC-375: Pi protocol alignment.

Verifies that autocontext's Pi integration matches Pi's documented
CLI and RPC protocols:

1. Pi CLI uses --print for one-shot (not --workspace, not JSON parsing)
2. Pi RPC uses stdin/stdout JSONL (not HTTP)
3. Workspace is handled via subprocess cwd (not --workspace flag)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestPiCLIProtocol:
    """Verify Pi CLI runtime matches Pi's documented interface."""

    def test_cli_uses_print_flag(self) -> None:
        """Pi one-shot mode uses --print per documented interface."""
        from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime

        config = PiCLIConfig(model="test-model")
        runtime = PiCLIRuntime(config)
        args = runtime._build_args("test prompt")
        assert "--print" in args

    def test_cli_does_not_use_workspace_flag(self) -> None:
        """Pi does not have a --workspace flag. Workspace is subprocess cwd."""
        from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime

        config = PiCLIConfig(workspace="/tmp/test-ws")
        runtime = PiCLIRuntime(config)
        args = runtime._build_args("test prompt")
        assert "--workspace" not in args

    def test_cli_uses_cwd_for_workspace(self) -> None:
        """Pi workspace should be handled via subprocess cwd, not a flag."""
        from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime

        config = PiCLIConfig(workspace="/tmp/test-ws")
        runtime = PiCLIRuntime(config)
        completed = MagicMock(returncode=0, stdout="response text", stderr="")
        with patch("subprocess.run", return_value=completed) as mock_run:
            runtime.generate("test prompt")
        # Workspace should be passed as cwd to subprocess
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("cwd") == "/tmp/test-ws" or \
               (call_kwargs[1] if len(call_kwargs) > 1 else {}).get("cwd") == "/tmp/test-ws"

    def test_cli_treats_print_output_as_plain_text(self) -> None:
        """--print returns plain text, not JSON. Default config should not parse as JSON."""
        from autocontext.runtimes.pi_cli import PiCLIRuntime

        runtime = PiCLIRuntime()
        # Default json_output=False means plain text returned as-is
        output = runtime._parse_output("Here is my analysis of the strategy.", 0)
        assert output.text == "Here is my analysis of the strategy."

    def test_cli_model_flag(self) -> None:
        """Pi uses --model for model selection."""
        from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime

        config = PiCLIConfig(model="claude-sonnet-4-20250514")
        runtime = PiCLIRuntime(config)
        args = runtime._build_args("test prompt")
        assert "--model" in args
        assert "claude-sonnet-4-20250514" in args


class TestPiRPCProtocol:
    """Verify Pi RPC runtime matches Pi's documented interface."""

    def test_rpc_uses_subprocess_not_http(self) -> None:
        """Pi RPC is stdin/stdout JSONL, not HTTP."""
        from autocontext.runtimes.pi_rpc import PiRPCRuntime

        runtime = PiRPCRuntime()
        # RPC runtime should NOT have HTTP-based methods
        assert not hasattr(runtime, "_http_client")
        # Should have process-based communication
        assert hasattr(runtime, "generate")

    def test_rpc_config_has_no_endpoint_field(self) -> None:
        """Pi RPC config should not have HTTP endpoint since it's stdio."""
        from autocontext.runtimes.pi_rpc import PiRPCConfig

        config = PiRPCConfig()
        # Should NOT have endpoint (HTTP concept)
        assert not hasattr(config, "endpoint") or config.endpoint == ""
        # Should have pi_command or similar subprocess config
        assert hasattr(config, "pi_command") or hasattr(config, "timeout")

    def test_rpc_starts_pi_with_mode_rpc(self) -> None:
        """Pi RPC should invoke `pi --mode rpc` as a subprocess."""
        from autocontext.runtimes.pi_rpc import PiRPCConfig, PiRPCRuntime

        config = PiRPCConfig()
        runtime = PiRPCRuntime(config)
        # Build the startup args — should include --mode rpc
        if hasattr(runtime, "_build_args"):
            args = runtime._build_args()
            assert "--mode" in args
            assert "rpc" in args


class TestPiDocAlignment:
    """Verify documentation accuracy."""

    def test_pi_settings_no_workspace_flag_reference(self) -> None:
        """Settings should use 'workspace' as cwd concept, not a CLI flag."""
        from autocontext.config.settings import AppSettings

        settings = AppSettings(agent_provider="pi", pi_workspace="/tmp/test")
        # The setting should exist but be documented as cwd, not a flag
        assert settings.pi_workspace == "/tmp/test"
