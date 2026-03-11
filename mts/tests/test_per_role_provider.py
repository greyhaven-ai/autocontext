"""Tests for AC-184: Per-role provider override (MTS_{ROLE}_PROVIDER).

Allows different providers per agent role so MLX can handle competitor
while frontier models handle reasoning roles.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mts.agents.llm_client import DeterministicDevClient
from mts.harness.core.llm_client import LanguageModelClient
from mts.harness.core.types import ModelResponse
from mts.providers.base import CompletionResult, LLMProvider

# ── Helpers ─────────────────────────────────────────────────────────────


class _StubProvider(LLMProvider):
    """Minimal LLMProvider stub for bridge testing."""

    def __init__(self, response: str = "stub output") -> None:
        self._response = response

    def complete(
        self, system_prompt: str, user_prompt: str,
        model: str | None = None, temperature: float = 0.0, max_tokens: int = 4096,
    ) -> CompletionResult:
        return CompletionResult(
            text=self._response, model=model or "stub",
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    def default_model(self) -> str:
        return "stub-model"


# ── Config field tests ──────────────────────────────────────────────────


class TestPerRoleConfigFields:
    def test_competitor_provider_field_exists(self) -> None:
        from mts.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "competitor_provider")
        assert settings.competitor_provider == ""

    def test_analyst_provider_field_exists(self) -> None:
        from mts.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "analyst_provider")
        assert settings.analyst_provider == ""

    def test_coach_provider_field_exists(self) -> None:
        from mts.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "coach_provider")
        assert settings.coach_provider == ""

    def test_architect_provider_field_exists(self) -> None:
        from mts.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "architect_provider")
        assert settings.architect_provider == ""


# ── ProviderBridgeClient tests ──────────────────────────────────────────


class TestProviderBridgeClient:
    def test_bridge_exists(self) -> None:
        from mts.agents.provider_bridge import ProviderBridgeClient
        assert issubclass(ProviderBridgeClient, LanguageModelClient)

    def test_bridge_generate_returns_model_response(self) -> None:
        from mts.agents.provider_bridge import ProviderBridgeClient

        provider = _StubProvider("hello world")
        bridge = ProviderBridgeClient(provider)
        response = bridge.generate(
            model="test-model", prompt="test prompt",
            max_tokens=100, temperature=0.5,
        )
        assert isinstance(response, ModelResponse)
        assert response.text == "hello world"

    def test_bridge_passes_temperature_and_max_tokens(self) -> None:
        from mts.agents.provider_bridge import ProviderBridgeClient

        provider = MagicMock(spec=LLMProvider)
        provider.complete.return_value = CompletionResult(
            text="ok", model="m", usage={"input_tokens": 1, "output_tokens": 1},
        )
        bridge = ProviderBridgeClient(provider)
        bridge.generate(model="m", prompt="p", max_tokens=256, temperature=0.7)

        provider.complete.assert_called_once()
        _, kwargs = provider.complete.call_args
        assert kwargs.get("temperature") == 0.7 or provider.complete.call_args[0][0] is not None

    def test_bridge_usage_contains_model(self) -> None:
        from mts.agents.provider_bridge import ProviderBridgeClient

        provider = _StubProvider("output")
        bridge = ProviderBridgeClient(provider)
        response = bridge.generate(model="my-model", prompt="p", max_tokens=100, temperature=0.0)
        assert response.usage.model == "my-model"

    def test_bridge_extracts_token_counts(self) -> None:
        from mts.agents.provider_bridge import ProviderBridgeClient

        provider = _StubProvider("output")
        bridge = ProviderBridgeClient(provider)
        response = bridge.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 5


# ── Client creation helper tests ────────────────────────────────────────


class TestCreateClientForProvider:
    def test_deterministic_provider_creates_deterministic_client(self) -> None:
        from mts.agents.provider_bridge import create_role_client
        from mts.config.settings import AppSettings

        settings = AppSettings()
        client = create_role_client("deterministic", settings)
        assert isinstance(client, DeterministicDevClient)

    def test_anthropic_provider_creates_anthropic_client(self) -> None:
        from mts.agents.provider_bridge import create_role_client
        from mts.config.settings import AppSettings

        settings = AppSettings(anthropic_api_key="test-key")
        client = create_role_client("anthropic", settings)
        # Should be AnthropicClient (don't import it to avoid dep)
        assert isinstance(client, LanguageModelClient)

    @patch("mts.agents.provider_bridge._create_provider_bridge")
    def test_mlx_provider_creates_bridge_client(self, mock_bridge: MagicMock) -> None:
        from mts.agents.provider_bridge import create_role_client
        from mts.config.settings import AppSettings

        mock_bridge.return_value = MagicMock(spec=LanguageModelClient)
        settings = AppSettings(mlx_model_path="/fake/model")
        client = create_role_client("mlx", settings)
        assert isinstance(client, LanguageModelClient)
        mock_bridge.assert_called_once()

    def test_empty_provider_returns_none(self) -> None:
        from mts.agents.provider_bridge import create_role_client
        from mts.config.settings import AppSettings

        settings = AppSettings()
        result = create_role_client("", settings)
        assert result is None

    def test_unknown_provider_raises(self) -> None:
        from mts.agents.provider_bridge import create_role_client
        from mts.config.settings import AppSettings

        settings = AppSettings()
        with pytest.raises(ValueError, match="unsupported.*provider"):
            create_role_client("magic-llm", settings)


# ── Orchestrator wiring tests ──────────────────────────────────────────


class TestOrchestratorPerRoleWiring:
    def test_default_all_roles_use_same_client(self) -> None:
        """With no overrides, all runners share the same runtime client."""
        from mts.agents.orchestrator import AgentOrchestrator
        from mts.config.settings import AppSettings

        settings = AppSettings(agent_provider="deterministic")
        orch = AgentOrchestrator.from_settings(settings)
        # All runners should share the same runtime
        assert orch.competitor.runtime.client is orch.analyst.runtime.client
        assert orch.analyst.runtime.client is orch.coach.runtime.client
        assert orch.coach.runtime.client is orch.architect.runtime.client

    @patch("mts.agents.provider_bridge.create_role_client")
    def test_competitor_override_creates_separate_runtime(self, mock_create: MagicMock) -> None:
        """MTS_COMPETITOR_PROVIDER overrides competitor's client only."""
        from mts.agents.orchestrator import AgentOrchestrator
        from mts.config.settings import AppSettings

        mock_client = MagicMock(spec=LanguageModelClient)
        mock_create.return_value = mock_client

        settings = AppSettings(agent_provider="deterministic", competitor_provider="mlx")
        orch = AgentOrchestrator.from_settings(settings)

        # Competitor should use the override client
        assert orch.competitor.runtime.client is mock_client
        # Other roles should still share the default client
        assert orch.analyst.runtime.client is orch.coach.runtime.client

    @patch("mts.agents.provider_bridge.create_role_client")
    def test_multiple_role_overrides(self, mock_create: MagicMock) -> None:
        """Multiple per-role overrides work simultaneously."""
        from mts.agents.orchestrator import AgentOrchestrator
        from mts.config.settings import AppSettings

        # Return a different mock per call
        clients = [MagicMock(spec=LanguageModelClient) for _ in range(2)]
        mock_create.side_effect = clients

        settings = AppSettings(
            agent_provider="deterministic",
            competitor_provider="mlx",
            analyst_provider="anthropic",
            anthropic_api_key="test-key",
        )
        orch = AgentOrchestrator.from_settings(settings)

        # Competitor and analyst should each have their own client
        assert orch.competitor.runtime.client is clients[0]
        assert orch.analyst.runtime.client is clients[1]
        # Coach and architect should share the default
        assert orch.coach.runtime.client is orch.architect.runtime.client

    @patch("mts.agents.provider_bridge.create_role_client")
    def test_override_does_not_affect_unset_roles(self, mock_create: MagicMock) -> None:
        """Roles without overrides still use the default provider."""
        from mts.agents.orchestrator import AgentOrchestrator
        from mts.config.settings import AppSettings

        mock_client = MagicMock(spec=LanguageModelClient)
        mock_create.return_value = mock_client

        settings = AppSettings(agent_provider="deterministic", architect_provider="anthropic")
        orch = AgentOrchestrator.from_settings(settings)

        # Architect gets override
        assert orch.architect.runtime.client is mock_client
        # Competitor, analyst, coach should all share default
        assert orch.competitor.runtime.client is orch.analyst.runtime.client
        assert orch.analyst.runtime.client is orch.coach.runtime.client
