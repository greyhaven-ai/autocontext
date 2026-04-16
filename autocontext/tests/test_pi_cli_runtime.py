"""Tests for AC-223: Pi CLI adapter for harness execution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.provider_bridge import RuntimeBridgeClient, create_role_client
from autocontext.config.settings import AppSettings
from autocontext.runtimes.base import AgentOutput
from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime
from autocontext.runtimes.pi_defaults import PI_DEFAULT_TIMEOUT_SECONDS

# ---------------------------------------------------------------------------
# PiCLIConfig defaults
# ---------------------------------------------------------------------------


def test_config_defaults() -> None:
    c = PiCLIConfig()
    assert c.pi_command == "pi"
    assert c.model == ""
    assert c.timeout == PI_DEFAULT_TIMEOUT_SECONDS
    assert c.json_output is True
    assert c.workspace == ""
    assert c.no_context_files is False
    assert c.extra_args == []


def test_config_custom_values() -> None:
    c = PiCLIConfig(pi_command="/usr/local/bin/pi", model="pi-turbo", timeout=60.0, workspace="/tmp/ws")
    assert c.pi_command == "/usr/local/bin/pi"
    assert c.model == "pi-turbo"
    assert c.timeout == 60.0
    assert c.workspace == "/tmp/ws"


# ---------------------------------------------------------------------------
# Settings fields
# ---------------------------------------------------------------------------


def test_settings_pi_fields_exist() -> None:
    s = AppSettings()
    assert s.pi_command == "pi"
    assert s.pi_timeout == PI_DEFAULT_TIMEOUT_SECONDS
    assert s.pi_workspace == ""
    assert s.pi_model == ""
    assert s.pi_no_context_files is False


# ---------------------------------------------------------------------------
# PiCLIRuntime.generate() — successful JSON output
# ---------------------------------------------------------------------------


def test_generate_json_output() -> None:
    runtime = PiCLIRuntime(PiCLIConfig())
    json_output = json.dumps({"result": "hello from pi", "model": "pi-1", "cost_usd": 0.01})
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json_output, stderr="")

    with patch("subprocess.run", return_value=mock_result), patch("shutil.which", return_value="/usr/bin/pi"):
        output = runtime.generate("test prompt")
    assert output.text == "hello from pi"
    assert output.model == "pi-1"
    assert output.cost_usd == 0.01


# ---------------------------------------------------------------------------
# PiCLIRuntime.generate() — raw text fallback
# ---------------------------------------------------------------------------


def test_generate_raw_text_fallback() -> None:
    runtime = PiCLIRuntime(PiCLIConfig(json_output=True))
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="plain text output\n", stderr="")

    with patch("subprocess.run", return_value=mock_result), patch("shutil.which", return_value="/usr/bin/pi"):
        output = runtime.generate("test prompt")
    assert output.text == "plain text output"
    assert output.model == "pi"


def test_generate_json_object_without_result_serializes_full_payload() -> None:
    runtime = PiCLIRuntime(PiCLIConfig())
    raw_payload = {
        "actions": [
            {"name": "review_request", "parameters": {}, "reasoning": "Start with intake."},
        ],
    }
    mock_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(raw_payload),
        stderr="",
    )

    with patch("subprocess.run", return_value=mock_result), patch("shutil.which", return_value="/usr/bin/pi"):
        output = runtime.generate("test prompt")

    assert json.loads(output.text) == raw_payload
    assert output.metadata["raw_json"] == raw_payload


def test_generate_json_output_disabled() -> None:
    runtime = PiCLIRuntime(PiCLIConfig(json_output=False))
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="raw output\n", stderr="")

    with patch("subprocess.run", return_value=mock_result), patch("shutil.which", return_value="/usr/bin/pi"):
        output = runtime.generate("test prompt")
    assert output.text == "raw output"


# ---------------------------------------------------------------------------
# PiCLIRuntime.generate() — timeout handling
# ---------------------------------------------------------------------------


def test_generate_timeout() -> None:
    runtime = PiCLIRuntime(PiCLIConfig(timeout=5.0))

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pi", timeout=5.0)):
        with patch("shutil.which", return_value="/usr/bin/pi"):
            output = runtime.generate("test prompt")
    assert output.text == ""
    assert output.metadata.get("error") == "timeout"


# ---------------------------------------------------------------------------
# PiCLIRuntime.generate() — non-zero exit code
# ---------------------------------------------------------------------------


def test_generate_nonzero_exit() -> None:
    runtime = PiCLIRuntime(PiCLIConfig())
    mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="segfault")

    with patch("subprocess.run", return_value=mock_result), patch("shutil.which", return_value="/usr/bin/pi"):
        output = runtime.generate("test prompt")
    assert output.text == ""
    assert output.metadata.get("error") == "nonzero_exit"
    assert output.metadata.get("exit_code") == 1


def test_generate_nonzero_exit_with_stdout() -> None:
    """Non-zero exit but stdout has content — use it."""
    runtime = PiCLIRuntime(PiCLIConfig())
    mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="partial output\n", stderr="warning")

    with patch("subprocess.run", return_value=mock_result), patch("shutil.which", return_value="/usr/bin/pi"):
        output = runtime.generate("test prompt")
    assert output.text == "partial output"


# ---------------------------------------------------------------------------
# PiCLIRuntime.revise()
# ---------------------------------------------------------------------------


def test_revise_builds_correct_prompt() -> None:
    runtime = PiCLIRuntime(PiCLIConfig())
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="revised output\n", stderr="")
    captured_args: list[str] = []

    def mock_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_args.extend(args)
        return mock_result

    with patch("subprocess.run", side_effect=mock_run), patch("shutil.which", return_value="/usr/bin/pi"):
        output = runtime.revise("original task", "old output", "fix the formatting")
    assert output.text == "revised output"
    # The prompt (last arg) should contain all revision parts
    prompt_arg = captured_args[-1]
    assert "original task" in prompt_arg
    assert "old output" in prompt_arg
    assert "fix the formatting" in prompt_arg


# ---------------------------------------------------------------------------
# PiCLIRuntime — pi binary not found
# ---------------------------------------------------------------------------


def test_generate_binary_not_found() -> None:
    runtime = PiCLIRuntime(PiCLIConfig(pi_command="nonexistent-pi"))

    with patch("subprocess.run", side_effect=FileNotFoundError), patch("shutil.which", return_value=None):
        output = runtime.generate("test")
    assert output.metadata.get("error") == "pi_not_found"


# ---------------------------------------------------------------------------
# PiCLIRuntime — command building
# ---------------------------------------------------------------------------


def test_build_args_includes_model_and_prompt() -> None:
    runtime = PiCLIRuntime(
        PiCLIConfig(
            model="pi-turbo",
            workspace="/tmp/ws",
            no_context_files=True,
            extra_args=["--verbose"],
        )
    )
    with patch("shutil.which", return_value="/usr/bin/pi"):
        args = runtime._build_args("test prompt")
    assert "--model" in args
    assert "pi-turbo" in args
    assert "--no-context-files" in args
    assert "--verbose" in args
    assert "test prompt" in args
    # workspace is NOT a Pi flag — handled via cwd
    assert "--workspace" not in args


def test_build_args_minimal() -> None:
    with patch("shutil.which", return_value="/usr/bin/pi"):
        runtime = PiCLIRuntime(PiCLIConfig())
    args = runtime._build_args("hello")
    assert args[:2] == ["/usr/bin/pi", "--print"]
    assert "--model" not in args
    assert "hello" in args


# ---------------------------------------------------------------------------
# RuntimeBridgeClient
# ---------------------------------------------------------------------------


def test_runtime_bridge_client_delegates() -> None:
    mock_runtime = MagicMock()
    mock_runtime.generate.return_value = MagicMock(text="bridge output", model="pi", metadata={})

    client = RuntimeBridgeClient(mock_runtime)
    resp = client.generate(model="ignored", prompt="test", max_tokens=100, temperature=0.5)
    assert resp.text == "bridge output"
    assert resp.usage.model == "pi"
    mock_runtime.generate.assert_called_once_with("test")


def test_runtime_bridge_client_raises_on_runtime_error() -> None:
    mock_runtime = MagicMock()
    mock_runtime.name = "PiCLIRuntime"
    mock_runtime.generate.return_value = AgentOutput(text="", metadata={"error": "timeout"})

    client = RuntimeBridgeClient(mock_runtime)
    with pytest.raises(RuntimeError, match="PiCLIRuntime failed: timeout"):
        client.generate(model="ignored", prompt="test", max_tokens=100, temperature=0.5)


# ---------------------------------------------------------------------------
# create_role_client("pi")
# ---------------------------------------------------------------------------


def test_create_role_client_pi() -> None:
    s = AppSettings(pi_command="/usr/bin/pi", pi_timeout=60.0)
    client = create_role_client("pi", s)
    assert isinstance(client, RuntimeBridgeClient)


def test_create_role_client_pi_uses_scenario_handoff(tmp_path: Path) -> None:
    s = AppSettings(
        knowledge_root=tmp_path / "knowledge",
        pi_command="/usr/bin/pi",
        pi_timeout=60.0,
    )
    with patch(
        "autocontext.providers.scenario_routing.resolve_pi_model",
        return_value=SimpleNamespace(checkpoint_path="/models/grid_ctf/pi-v2"),
    ) as mock_resolve:
        client = create_role_client("pi", s, scenario_name="grid_ctf")

    assert isinstance(client, RuntimeBridgeClient)
    assert client._runtime._config.model == "/models/grid_ctf/pi-v2"  # type: ignore[attr-defined]
    mock_resolve.assert_called_once()


# ---------------------------------------------------------------------------
# PiCLIRuntime.available property
# ---------------------------------------------------------------------------


def test_available_when_found() -> None:
    with patch("shutil.which", return_value="/usr/bin/pi"):
        runtime = PiCLIRuntime(PiCLIConfig())
    assert runtime.available is True


def test_not_available_when_missing() -> None:
    with patch("shutil.which", return_value=None):
        runtime = PiCLIRuntime(PiCLIConfig())
    assert runtime.available is False


# ---------------------------------------------------------------------------
# generate with system prompt
# ---------------------------------------------------------------------------


def test_generate_with_system_prompt() -> None:
    runtime = PiCLIRuntime(PiCLIConfig())
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="output\n", stderr="")
    captured_args: list[str] = []

    def mock_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_args.extend(args)
        return mock_result

    with patch("subprocess.run", side_effect=mock_run), patch("shutil.which", return_value="/usr/bin/pi"):
        runtime.generate("user prompt", system="system prompt")
    # System + user prompt should be combined in the last arg
    prompt_arg = captured_args[-1]
    assert "system prompt" in prompt_arg
    assert "user prompt" in prompt_arg
