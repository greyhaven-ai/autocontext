"""Tests for AC-357: Expose Pi and Pi-RPC through the main agent provider surface.

Verifies that ``build_client_from_settings`` accepts ``pi`` and ``pi-rpc``
as first-class top-level provider choices.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.llm_client import build_client_from_settings
from autocontext.config.settings import AppSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**overrides: object) -> AppSettings:
    """Build an AppSettings with sensible defaults and overrides."""
    defaults = {
        "agent_provider": "deterministic",
        "knowledge_root": Path("/tmp/ac-test-knowledge"),
    }
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Pi CLI happy path
# ---------------------------------------------------------------------------

class TestPiCLIProvider:
    def test_build_client_accepts_pi_provider(self) -> None:
        """``AUTOCONTEXT_AGENT_PROVIDER=pi`` should construct a valid client."""
        settings = _settings(agent_provider="pi", pi_command="pi", pi_timeout=30.0)
        with patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert client is not None

    def test_pi_client_is_runtime_bridge(self) -> None:
        """The returned client should be a RuntimeBridgeClient wrapping PiCLIRuntime."""
        settings = _settings(agent_provider="pi", pi_command="pi")
        with patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        from autocontext.agents.provider_bridge import RuntimeBridgeClient
        assert isinstance(client, RuntimeBridgeClient)

    def test_pi_passes_config_from_settings(self) -> None:
        """Pi CLI config should use settings values for command, timeout, workspace."""
        settings = _settings(
            agent_provider="pi",
            pi_command="/usr/local/bin/pi",
            pi_timeout=60.0,
            pi_workspace="/my/workspace",
            pi_model="local-model",
        )
        with patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings)
        call_args = MockRuntime.call_args
        config = call_args[0][0] if call_args[0] else call_args[1].get("config")
        assert config.pi_command == "/usr/local/bin/pi"
        assert config.timeout == 60.0
        assert config.workspace == "/my/workspace"

    def test_pi_resolves_scenario_model_handoff(self) -> None:
        """Scenario-aware Pi clients should resolve the active checkpoint via the registry."""
        settings = _settings(agent_provider="pi")
        with (
            patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime,
            patch(
                "autocontext.providers.scenario_routing.resolve_pi_model",
                return_value=SimpleNamespace(checkpoint_path="/models/grid-ctf/pi-v4"),
            ) as mock_resolve,
        ):
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings, scenario_name="grid_ctf")
        call_args = MockRuntime.call_args
        config = call_args[0][0] if call_args[0] else call_args[1].get("config")
        assert config.model == "/models/grid-ctf/pi-v4"
        assert mock_resolve.call_args.kwargs["scenario"] == "grid_ctf"
        assert mock_resolve.call_args.kwargs["manual_override"] is None
        assert client is not None


# ---------------------------------------------------------------------------
# Pi RPC happy path
# ---------------------------------------------------------------------------

class TestPiRPCProvider:
    def test_build_client_accepts_pi_rpc_provider(self) -> None:
        """``AUTOCONTEXT_AGENT_PROVIDER=pi-rpc`` should construct a valid client."""
        settings = _settings(
            agent_provider="pi-rpc",
            pi_rpc_endpoint="http://localhost:3284",
        )
        with patch("autocontext.runtimes.pi_rpc.PiRPCRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert client is not None

    def test_pi_rpc_client_is_runtime_bridge(self) -> None:
        """The returned client should be a RuntimeBridgeClient wrapping PiRPCRuntime."""
        settings = _settings(
            agent_provider="pi-rpc",
            pi_rpc_endpoint="http://localhost:3284",
        )
        with patch("autocontext.runtimes.pi_rpc.PiRPCRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        from autocontext.agents.provider_bridge import RuntimeBridgeClient
        assert isinstance(client, RuntimeBridgeClient)

    def test_pi_rpc_passes_config_from_settings(self) -> None:
        """Pi RPC config should use settings values for session_persistence."""
        settings = _settings(
            agent_provider="pi-rpc",
            pi_rpc_session_persistence=False,
        )
        with patch("autocontext.runtimes.pi_rpc.PiRPCRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings)
        call_args = MockRuntime.call_args
        config = call_args[0][0] if call_args[0] else call_args[1].get("config")
        assert config.session_persistence is False
        assert config.pi_command == "pi"


# ---------------------------------------------------------------------------
# Misconfiguration
# ---------------------------------------------------------------------------

class TestPiMisconfiguration:
    def test_unknown_provider_still_raises(self) -> None:
        """An unsupported provider type should still raise ValueError."""
        settings = _settings(agent_provider="nonexistent-provider")
        with pytest.raises(ValueError, match="unsupported agent provider"):
            build_client_from_settings(settings)
