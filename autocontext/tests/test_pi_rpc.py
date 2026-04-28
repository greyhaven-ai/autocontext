"""Tests for Pi RPC runtime — stdin/stdout JSONL protocol (AC-375).

Updated from the original AC-225 HTTP-based tests to match Pi's
actual documented RPC protocol: subprocess communication over
stdin/stdout with JSONL framing.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

from autocontext.agents.provider_bridge import RuntimeBridgeClient, create_role_client
from autocontext.config.settings import AppSettings
from autocontext.runtimes.pi_rpc import PiPersistentRPCRuntime, PiRPCConfig, PiRPCRuntime


class _FakeStdin:
    def __init__(self) -> None:
        self.value = ""
        self.closed = False

    def write(self, text: str) -> int:
        self.value += text
        return len(text)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0, *, never_exits: bool = False) -> None:
        self.stdin = _FakeStdin()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = None if never_exits else returncode
        self._returncode = returncode
        self._never_exits = never_exits
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = self._returncode
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

# ---------------------------------------------------------------------------
# PiRPCConfig defaults
# ---------------------------------------------------------------------------


def test_config_defaults() -> None:
    c = PiRPCConfig()
    assert c.pi_command == "pi"
    assert c.timeout == 120.0
    assert c.workspace == ""
    assert c.session_persistence is True
    assert c.no_context_files is False
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


def test_build_args_no_context_files() -> None:
    runtime = PiRPCRuntime(PiRPCConfig(no_context_files=True))
    args = runtime._build_args()
    assert "--no-context-files" in args


# ---------------------------------------------------------------------------
# PiRPCRuntime.generate() — mocked subprocess
# ---------------------------------------------------------------------------


def test_generate_success() -> None:
    """generate() sends JSONL command and parses response."""
    runtime = PiRPCRuntime()
    rpc_response = "\n".join(
        [
            json.dumps({"type": "response", "command": "prompt", "success": True}),
            json.dumps(
                {
                    "type": "agent_end",
                    "messages": [{"role": "assistant", "content": "Strategy analysis complete."}],
                }
            ),
        ]
    )
    process = _FakePopen(rpc_response + "\n")
    with patch("subprocess.Popen", return_value=process):
        output = runtime.generate("Analyze this strategy")
    sent = json.loads(process.stdin.value)
    assert sent["message"] == "Analyze this strategy"
    assert "content" not in sent
    assert process.stdin.closed is True
    assert output.text == "Strategy analysis complete."
    assert output.metadata["exit_code"] == 0


def test_generate_timeout() -> None:
    """generate() handles subprocess timeout gracefully."""
    runtime = PiRPCRuntime(PiRPCConfig(timeout=0.01))
    process = _FakePopen("", never_exits=True)
    with patch("subprocess.Popen", return_value=process):
        output = runtime.generate("test")
    assert output.text == ""
    assert output.metadata.get("error") == "timeout"
    assert process.killed is True


def test_generate_rpc_error_response() -> None:
    """generate() surfaces Pi RPC error responses as errors, not model text."""
    runtime = PiRPCRuntime()
    rpc_response = json.dumps(
        {
            "type": "response",
            "command": "prompt",
            "success": False,
            "error": "bad payload",
        }
    )
    process = _FakePopen(rpc_response + "\n")
    with patch("subprocess.Popen", return_value=process):
        output = runtime.generate("test")
    assert output.text == ""
    assert output.metadata["error"] == "rpc_response_error"
    assert output.metadata["rpc_command"] == "prompt"
    assert output.metadata["rpc_message"] == "bad payload"


def test_generate_nonzero_exit_without_stdout() -> None:
    """generate() surfaces transport/process failures when Pi exits non-zero."""
    runtime = PiRPCRuntime()
    process = _FakePopen("", stderr="permission denied", returncode=2)
    with patch("subprocess.Popen", return_value=process):
        output = runtime.generate("test")
    assert output.text == ""
    assert output.metadata["error"] == "nonzero_exit"
    assert output.metadata["exit_code"] == 2
    assert output.metadata["stderr"] == "permission denied"


def test_generate_prompt_ack_without_assistant_response_is_error() -> None:
    """The prompt ack is not the final model response."""
    runtime = PiRPCRuntime()
    rpc_response = json.dumps({"type": "response", "command": "prompt", "success": True})
    process = _FakePopen(rpc_response + "\n")
    with patch("subprocess.Popen", return_value=process):
        output = runtime.generate("test")
    assert output.text == ""
    assert output.metadata["error"] == "missing_assistant_response"


def test_revise_success() -> None:
    """revise() sends a revision prompt through generate()."""
    runtime = PiRPCRuntime()
    rpc_response = json.dumps(
        {
            "type": "agent_end",
            "messages": [{"role": "assistant", "content": "Revised output."}],
        }
    )
    process = _FakePopen(rpc_response + "\n")
    with patch("subprocess.Popen", return_value=process):
        output = runtime.revise("original", "prev output", "feedback")
    assert output.text == "Revised output."


# ---------------------------------------------------------------------------
# PiPersistentRPCRuntime — process reuse and queue/state commands
# ---------------------------------------------------------------------------


def _event_line(event: dict[str, object]) -> str:
    return json.dumps(event) + "\n"


def test_persistent_runtime_reuses_process_for_multiple_prompts() -> None:
    """Persistent RPC keeps one Pi process alive across prompts."""
    stdout = "".join(
        [
            _event_line({"type": "response", "command": "prompt", "success": True}),
            _event_line({"type": "agent_end", "messages": [{"role": "assistant", "content": "first"}]}),
            _event_line({"type": "response", "command": "prompt", "success": True}),
            _event_line({"type": "agent_end", "messages": [{"role": "assistant", "content": "second"}]}),
        ]
    )
    process = _FakePopen(stdout, never_exits=True)
    runtime = PiPersistentRPCRuntime(PiRPCConfig(timeout=0.5))
    with patch("subprocess.Popen", return_value=process) as popen:
        first = runtime.generate("one")
        second = runtime.generate("two")
        runtime.close()

    sent_lines = [json.loads(line) for line in process.stdin.value.strip().split("\n")]
    assert popen.call_count == 1
    assert [line["message"] for line in sent_lines] == ["one", "two"]
    assert first.text == "first"
    assert second.text == "second"
    assert process.stdin.closed is True


def test_persistent_runtime_supports_steer_follow_up_and_state_commands() -> None:
    """Persistent RPC exposes Pi queue and state commands instead of only prompt()."""
    stdout = "".join(
        [
            _event_line({"type": "response", "command": "steer", "success": True}),
            _event_line({"type": "response", "command": "follow_up", "success": True}),
            _event_line(
                {
                    "type": "response",
                    "command": "get_state",
                    "success": True,
                    "data": {"isStreaming": True, "pendingMessageCount": 2},
                }
            ),
        ]
    )
    process = _FakePopen(stdout, never_exits=True)
    runtime = PiPersistentRPCRuntime(PiRPCConfig(timeout=0.5))
    with patch("subprocess.Popen", return_value=process):
        assert runtime.steer("change course")["success"] is True
        assert runtime.follow_up("also check docs")["success"] is True
        state = runtime.get_state()
        runtime.close()

    sent_lines = [json.loads(line) for line in process.stdin.value.strip().split("\n")]
    assert [line["type"] for line in sent_lines] == ["steer", "follow_up", "get_state"]
    assert sent_lines[0]["message"] == "change course"
    assert sent_lines[1]["message"] == "also check docs"
    assert state["isStreaming"] is True
    assert state["pendingMessageCount"] == 2


def test_persistent_runtime_workspace_is_subprocess_cwd() -> None:
    """Pi RPC uses cwd for workspace, matching Pi CLI protocol alignment."""
    process = _FakePopen(
        _event_line({"type": "response", "command": "get_state", "success": True, "data": {}}),
        never_exits=True,
    )
    runtime = PiPersistentRPCRuntime(PiRPCConfig(workspace="/tmp/pi-ws", timeout=0.5))
    with patch("subprocess.Popen", return_value=process) as popen:
        runtime.get_state()
        runtime.close()

    assert popen.call_args.kwargs["cwd"] == "/tmp/pi-ws"


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
