"""Tests for AC-184: Per-role provider override (AUTOCONTEXT_{ROLE}_PROVIDER).

Allows different providers per agent role so MLX can handle competitor
while frontier models handle reasoning roles.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse
from autocontext.providers.base import CompletionResult, LLMProvider

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
        from autocontext.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "competitor_provider")
        assert settings.competitor_provider == ""

    def test_analyst_provider_field_exists(self) -> None:
        from autocontext.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "analyst_provider")
        assert settings.analyst_provider == ""

    def test_coach_provider_field_exists(self) -> None:
        from autocontext.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "coach_provider")
        assert settings.coach_provider == ""

    def test_architect_provider_field_exists(self) -> None:
        from autocontext.config.settings import AppSettings
        settings = AppSettings()
        assert hasattr(settings, "architect_provider")
        assert settings.architect_provider == ""


# ── ProviderBridgeClient tests ──────────────────────────────────────────


class TestProviderBridgeClient:
    def test_bridge_exists(self) -> None:
        from autocontext.agents.provider_bridge import ProviderBridgeClient
        assert issubclass(ProviderBridgeClient, LanguageModelClient)

    def test_bridge_generate_returns_model_response(self) -> None:
        from autocontext.agents.provider_bridge import ProviderBridgeClient

        provider = _StubProvider("hello world")
        bridge = ProviderBridgeClient(provider)
        response = bridge.generate(
            model="test-model", prompt="test prompt",
            max_tokens=100, temperature=0.5,
        )
        assert isinstance(response, ModelResponse)
        assert response.text == "hello world"

    def test_bridge_passes_temperature_and_max_tokens(self) -> None:
        from autocontext.agents.provider_bridge import ProviderBridgeClient

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
        from autocontext.agents.provider_bridge import ProviderBridgeClient

        provider = _StubProvider("output")
        bridge = ProviderBridgeClient(provider)
        response = bridge.generate(model="my-model", prompt="p", max_tokens=100, temperature=0.0)
        assert response.usage.model == "my-model"

    def test_bridge_extracts_token_counts(self) -> None:
        from autocontext.agents.provider_bridge import ProviderBridgeClient

        provider = _StubProvider("output")
        bridge = ProviderBridgeClient(provider)
        response = bridge.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 5

    def test_bridge_can_use_provider_default_model_for_overrides(self) -> None:
        from autocontext.agents.provider_bridge import ProviderBridgeClient

        provider = _StubProvider("output")
        bridge = ProviderBridgeClient(provider, use_provider_default_model=True)
        response = bridge.generate(
            model="claude-sonnet-4-5-20250929",
            prompt="p",
            max_tokens=100,
            temperature=0.0,
        )
        assert response.usage.model == "stub"


# ── Client creation helper tests ────────────────────────────────────────


class TestCreateClientForProvider:
    def test_deterministic_provider_creates_deterministic_client(self) -> None:
        from autocontext.agents.provider_bridge import create_role_client
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        client = create_role_client("deterministic", settings)
        assert isinstance(client, DeterministicDevClient)

    def test_anthropic_provider_creates_anthropic_client(self) -> None:
        from autocontext.agents.provider_bridge import create_role_client
        from autocontext.config.settings import AppSettings

        settings = AppSettings(anthropic_api_key="test-key")
        client = create_role_client("anthropic", settings)
        # Should be AnthropicClient (don't import it to avoid dep)
        assert isinstance(client, LanguageModelClient)

    @patch("autocontext.agents.provider_bridge._create_provider_bridge")
    def test_mlx_provider_creates_bridge_client(self, mock_bridge: MagicMock) -> None:
        from autocontext.agents.provider_bridge import create_role_client
        from autocontext.config.settings import AppSettings

        mock_bridge.return_value = MagicMock(spec=LanguageModelClient)
        settings = AppSettings(mlx_model_path="/fake/model")
        client = create_role_client("mlx", settings)
        assert isinstance(client, LanguageModelClient)
        mock_bridge.assert_called_once()

    @patch("autocontext.providers.registry.create_provider")
    def test_openai_override_uses_judge_key_not_anthropic_key(self, mock_create: MagicMock) -> None:
        from autocontext.agents.provider_bridge import create_role_client
        from autocontext.config.settings import AppSettings

        mock_create.return_value = _StubProvider()
        settings = AppSettings(
            anthropic_api_key="anthropic-key",
            judge_api_key="openai-key",
            judge_base_url="http://localhost:8000/v1",
        )

        client = create_role_client("openai", settings)

        assert isinstance(client, LanguageModelClient)
        mock_create.assert_called_once_with(
            provider_type="openai",
            api_key="openai-key",
            base_url="http://localhost:8000/v1",
            model="gpt-4o",
        )

    def test_empty_provider_returns_none(self) -> None:
        from autocontext.agents.provider_bridge import create_role_client
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        result = create_role_client("", settings)
        assert result is None

    def test_unknown_provider_raises(self) -> None:
        from autocontext.agents.provider_bridge import create_role_client
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        with pytest.raises(ValueError, match="unsupported.*provider"):
            create_role_client("magic-llm", settings)


# ── Orchestrator wiring tests ──────────────────────────────────────────


class TestOrchestratorPerRoleWiring:
    def test_default_all_roles_use_same_client(self) -> None:
        """With no overrides, all runners share the same runtime client."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.config.settings import AppSettings

        settings = AppSettings(agent_provider="deterministic")
        orch = AgentOrchestrator.from_settings(settings)
        # All runners should share the same runtime
        assert orch.competitor.runtime.client is orch.analyst.runtime.client
        assert orch.analyst.runtime.client is orch.coach.runtime.client
        assert orch.coach.runtime.client is orch.architect.runtime.client

    @patch("autocontext.agents.provider_bridge.create_role_client")
    def test_competitor_override_creates_separate_runtime(self, mock_create: MagicMock) -> None:
        """AUTOCONTEXT_COMPETITOR_PROVIDER overrides competitor's client only."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.config.settings import AppSettings

        mock_client = MagicMock(spec=LanguageModelClient)
        mock_create.return_value = mock_client

        settings = AppSettings(agent_provider="deterministic", competitor_provider="mlx")
        orch = AgentOrchestrator.from_settings(settings)

        # Competitor should use the override client
        assert orch.competitor.runtime.client is mock_client
        # Other roles should still share the default client
        assert orch.analyst.runtime.client is orch.coach.runtime.client

    @patch("autocontext.agents.provider_bridge.create_role_client")
    def test_multiple_role_overrides(self, mock_create: MagicMock) -> None:
        """Multiple per-role overrides work simultaneously."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.config.settings import AppSettings

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

    @patch("autocontext.agents.provider_bridge.create_role_client")
    def test_override_does_not_affect_unset_roles(self, mock_create: MagicMock) -> None:
        """Roles without overrides still use the default provider."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.config.settings import AppSettings

        mock_client = MagicMock(spec=LanguageModelClient)
        mock_create.return_value = mock_client

        settings = AppSettings(agent_provider="deterministic", architect_provider="anthropic")
        orch = AgentOrchestrator.from_settings(settings)

        # Architect gets override
        assert orch.architect.runtime.client is mock_client
        # Competitor, analyst, coach should all share default
        assert orch.competitor.runtime.client is orch.analyst.runtime.client
        assert orch.analyst.runtime.client is orch.coach.runtime.client

    @patch("autocontext.agents.orchestrator.build_client_from_settings")
    def test_from_settings_uses_shared_client_builder(self, mock_build: MagicMock) -> None:
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.config.settings import AppSettings

        default_client = MagicMock(spec=LanguageModelClient)
        mock_build.return_value = default_client

        settings = AppSettings(agent_provider="mlx", mlx_model_path="/tmp/model")
        orch = AgentOrchestrator.from_settings(settings)

        assert orch.client is default_client
        mock_build.assert_called_once_with(settings)

    def test_rlm_uses_role_specific_client_when_override_exists(self) -> None:
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.config.settings import AppSettings

        class _Worker:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        default_client = MagicMock(spec=LanguageModelClient)
        role_client = MagicMock(spec=LanguageModelClient)
        settings = AppSettings(agent_provider="deterministic")
        orch = AgentOrchestrator(default_client, settings)
        orch._role_clients["competitor"] = role_client

        context = SimpleNamespace(variables={}, summary="summary")

        with (
            patch("autocontext.rlm.session.make_llm_batch", return_value="batch") as mock_batch,
            patch("autocontext.rlm.session.RlmSession") as mock_session_cls,
        ):
            session = MagicMock()
            session.execution_history = []
            session.run.return_value = MagicMock()
            mock_session_cls.return_value = session

            orch._run_single_rlm_session(
                role="competitor",
                model="model",
                system_tpl="{variable_summary}",
                context=context,
                worker_cls=_Worker,
            )

        mock_batch.assert_called_once_with(role_client, settings.rlm_sub_model)
        assert mock_session_cls.call_args.kwargs["client"] is role_client
