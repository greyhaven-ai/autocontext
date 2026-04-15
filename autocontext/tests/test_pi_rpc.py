"""Tests for Pi RPC runtime — stdin/stdout JSONL protocol (AC-375).

Updated from the original AC-225 HTTP-based tests to match Pi's
actual documented RPC protocol: subprocess communication over
stdin/stdout with JSONL framing.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from autocontext.agents.provider_bridge import RuntimeBridgeClient, create_role_client
from autocontext.config.settings import AppSettings
from autocontext.runtimes.pi_rpc import PiRPCConfig, PiRPCRuntime

# ---------------------------------------------------------------------------
# PiRPCConfig defaults
# ---------------------------------------------------------------------------

def test_config_defaults() -> None:
    c = PiRPCConfig()
    assert c.pi_command == "pi"
    assert c.timeout == 120.0
    assert c.session_persistence is True
    assert c.branch_on_retry is True


# ---------------------------------------------------------------------------
# Settings fields
# ---------------------------------------------------------------------------

def test_settings_pi_rpc_fields() -> None:
    s = AppSettings()
    assert s.pi_rpc_endpoint == ""
    assert s.pi_rpc_api_key == ""
    assert s.pi_rpc_session_persistence is True


# ---------------------------------------------------------------------------
# PiRPCRuntime build_args
# ---------------------------------------------------------------------------

def test_build_args_includes_mode_rpc() -> None:
    runtime = PiRPCRuntime()
    args = runtime._build_args()
    assert "--mode" in args
    assert "rpc" in args


def test_build_args_includes_model() -> None:
    runtime = PiRPCRuntime(PiRPCConfig(model="test-model"))
    args = runtime._build_args()
    assert "--model" in args
    assert "test-model" in args


def test_build_args_no_session() -> None:
    runtime = PiRPCRuntime(PiRPCConfig(session_persistence=False))
    args = runtime._build_args()
    assert "--no-session" in args


# ---------------------------------------------------------------------------
# PiRPCRuntime.generate() — mocked subprocess
# ---------------------------------------------------------------------------

def test_generate_success() -> None:
    """generate() sends JSONL command and parses response."""
    runtime = PiRPCRuntime()
    rpc_response = json.dumps({
        "type": "agent_end",
        "messages": [{"role": "assistant", "content": "Strategy analysis complete."}],
    })
    completed = MagicMock(returncode=0, stdout=rpc_response + "\n", stderr="")
    with patch("subprocess.run", return_value=completed) as mock_run:
        output = runtime.generate("Analyze this strategy")
    sent = json.loads(mock_run.call_args.kwargs["input"])
    assert sent["message"] == "Analyze this strategy"
    assert "content" not in sent
    assert output.text == "Strategy analysis complete."
    assert output.metadata["exit_code"] == 0


def test_generate_timeout() -> None:
    """generate() handles subprocess timeout gracefully."""
    import subprocess as sp

    runtime = PiRPCRuntime()
    with patch("subprocess.run", side_effect=sp.TimeoutExpired("pi", 120)):
        output = runtime.generate("test")
    assert output.text == ""
    assert output.metadata.get("error") == "timeout"


def test_generate_rpc_error_response() -> None:
    """generate() surfaces Pi RPC error responses as errors, not model text."""
    runtime = PiRPCRuntime()
    rpc_response = json.dumps({
        "type": "response",
        "command": "prompt",
        "success": False,
        "error": "bad payload",
    })
    completed = MagicMock(returncode=0, stdout=rpc_response + "\n", stderr="")
    with patch("subprocess.run", return_value=completed):
        output = runtime.generate("test")
    assert output.text == ""
    assert output.metadata["error"] == "rpc_response_error"
    assert output.metadata["rpc_command"] == "prompt"
    assert output.metadata["rpc_message"] == "bad payload"


def test_generate_nonzero_exit_without_stdout() -> None:
    """generate() surfaces transport/process failures when Pi exits non-zero."""
    runtime = PiRPCRuntime()
    completed = MagicMock(returncode=2, stdout="", stderr="permission denied")
    with patch("subprocess.run", return_value=completed):
        output = runtime.generate("test")
    assert output.text == ""
    assert output.metadata["error"] == "nonzero_exit"
    assert output.metadata["exit_code"] == 2
    assert output.metadata["stderr"] == "permission denied"


def test_revise_success() -> None:
    """revise() sends a revision prompt through generate()."""
    runtime = PiRPCRuntime()
    rpc_response = json.dumps({
        "type": "agent_end",
        "messages": [{"role": "assistant", "content": "Revised output."}],
    })
    completed = MagicMock(returncode=0, stdout=rpc_response + "\n", stderr="")
    with patch("subprocess.run", return_value=completed):
        output = runtime.revise("original", "prev output", "feedback")
    assert output.text == "Revised output."


# ---------------------------------------------------------------------------
# create_role_client integration
# ---------------------------------------------------------------------------

def test_create_role_client_pi_rpc() -> None:
    """create_role_client('pi-rpc') should return a RuntimeBridgeClient."""
    settings = AppSettings(pi_timeout=240.0)
    with patch("autocontext.runtimes.pi_rpc.PiRPCRuntime") as MockRuntime:
        MockRuntime.return_value = MagicMock()
        client = create_role_client("pi-rpc", settings)
    assert isinstance(client, RuntimeBridgeClient)
    config = MockRuntime.call_args.args[0]
    assert config.timeout == 240.0
