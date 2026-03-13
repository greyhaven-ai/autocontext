"""Tests for AC-225: Spike Pi RPC/session integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from autocontext.agents.provider_bridge import RuntimeBridgeClient, create_role_client
from autocontext.config.settings import AppSettings
from autocontext.runtimes.pi_rpc import PiRPCConfig, PiRPCRuntime

# ---------------------------------------------------------------------------
# PiRPCConfig defaults
# ---------------------------------------------------------------------------


def test_config_defaults() -> None:
    c = PiRPCConfig()
    assert c.endpoint == "http://localhost:3284"
    assert c.api_key == ""
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
# PiRPCRuntime.generate() — mocked HTTP
# ---------------------------------------------------------------------------


def test_generate_success() -> None:
    runtime = PiRPCRuntime(PiRPCConfig(endpoint="http://mock:3284"))
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": "generated text", "model": "pi-rpc", "session_id": "s1"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response) as mock_post:
        output = runtime.generate("test prompt", system="sys prompt")
    assert output.text == "generated text"
    assert output.model == "pi-rpc"
    assert output.session_id == "s1"
    call_kwargs = mock_post.call_args
    payload = call_kwargs[1]["json"]
    assert payload["prompt"] == "test prompt"
    assert payload["system"] == "sys prompt"


def test_generate_timeout() -> None:
    import httpx

    runtime = PiRPCRuntime(PiRPCConfig(endpoint="http://mock:3284"))
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        output = runtime.generate("test")
    assert output.metadata.get("error") == "timeout"


# ---------------------------------------------------------------------------
# PiRPCRuntime.revise() — mocked HTTP
# ---------------------------------------------------------------------------


def test_revise_success() -> None:
    runtime = PiRPCRuntime(PiRPCConfig(endpoint="http://mock:3284", branch_on_retry=False))
    mock_response = MagicMock()
    mock_response.json.return_value = {"result": "revised", "model": "pi"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response):
        output = runtime.revise("task", "old output", "fix it")
    assert output.text == "revised"


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


def test_create_session() -> None:
    runtime = PiRPCRuntime(PiRPCConfig(endpoint="http://mock:3284"))
    mock_response = MagicMock()
    mock_response.json.return_value = {"session_id": "new-session-123"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response):
        sid = runtime.create_session()
    assert sid == "new-session-123"
    assert runtime._current_session_id == "new-session-123"


def test_branch_session() -> None:
    runtime = PiRPCRuntime(PiRPCConfig(endpoint="http://mock:3284"))
    runtime._current_session_id = "original"
    mock_response = MagicMock()
    mock_response.json.return_value = {"branch_id": "branch-456"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response):
        bid = runtime.branch_session("original")
    assert bid == "branch-456"
    assert runtime._current_session_id == "branch-456"


def test_resume_session() -> None:
    runtime = PiRPCRuntime(PiRPCConfig())
    runtime.resume_session("existing-session")
    assert runtime._current_session_id == "existing-session"


# ---------------------------------------------------------------------------
# create_role_client("pi-rpc")
# ---------------------------------------------------------------------------


def test_create_role_client_pi_rpc() -> None:
    s = AppSettings(pi_rpc_endpoint="http://pi:3284", pi_rpc_api_key="key-123")
    client = create_role_client("pi-rpc", s)
    assert isinstance(client, RuntimeBridgeClient)
