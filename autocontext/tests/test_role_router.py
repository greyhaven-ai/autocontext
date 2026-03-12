"""Tests for AC-204: Capability- and cost-aware role routing."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# TestProviderClass
# ---------------------------------------------------------------------------


class TestProviderClass:
    def test_all_classes_defined(self) -> None:
        from autocontext.agents.role_router import ProviderClass

        assert ProviderClass.FRONTIER == "frontier"
        assert ProviderClass.MID_TIER == "mid_tier"
        assert ProviderClass.FAST == "fast"
        assert ProviderClass.LOCAL == "local"
        assert ProviderClass.CODE_POLICY == "code_policy"


# ---------------------------------------------------------------------------
# TestProviderConfig
# ---------------------------------------------------------------------------


class TestProviderConfig:
    def test_create_config(self) -> None:
        from autocontext.agents.role_router import ProviderClass, ProviderConfig

        cfg = ProviderConfig(
            provider_type="anthropic",
            model="claude-opus-4-6",
            provider_class=ProviderClass.FRONTIER,
            estimated_cost_per_1k_tokens=0.015,
        )
        assert cfg.provider_type == "anthropic"
        assert cfg.model == "claude-opus-4-6"
        assert cfg.provider_class == ProviderClass.FRONTIER
        assert cfg.estimated_cost_per_1k_tokens == 0.015

    def test_config_with_none_model(self) -> None:
        from autocontext.agents.role_router import ProviderClass, ProviderConfig

        cfg = ProviderConfig(
            provider_type="mlx",
            model=None,
            provider_class=ProviderClass.LOCAL,
            estimated_cost_per_1k_tokens=0.0,
        )
        assert cfg.model is None


# ---------------------------------------------------------------------------
# TestRoutingContext
# ---------------------------------------------------------------------------


class TestRoutingContext:
    def test_defaults(self) -> None:
        from autocontext.agents.role_router import RoutingContext

        ctx = RoutingContext()
        assert ctx.generation == 0
        assert ctx.retry_count == 0
        assert ctx.is_plateau is False
        assert ctx.available_local_models == []
        assert ctx.scenario_name == ""

    def test_with_artifacts(self) -> None:
        from autocontext.agents.role_router import RoutingContext

        ctx = RoutingContext(
            available_local_models=["distilled_grid_ctf"],
            scenario_name="grid_ctf",
        )
        assert len(ctx.available_local_models) == 1


# ---------------------------------------------------------------------------
# TestDefaultRoutingTable
# ---------------------------------------------------------------------------


class TestDefaultRoutingTable:
    def test_default_table_has_all_roles(self) -> None:
        from autocontext.agents.role_router import DEFAULT_ROUTING_TABLE

        expected_roles = {"competitor", "analyst", "coach", "architect", "curator", "translator"}
        assert expected_roles == set(DEFAULT_ROUTING_TABLE.keys())

    def test_competitor_prefers_frontier(self) -> None:
        from autocontext.agents.role_router import DEFAULT_ROUTING_TABLE, ProviderClass

        assert DEFAULT_ROUTING_TABLE["competitor"][0] == ProviderClass.FRONTIER

    def test_analyst_prefers_mid_tier(self) -> None:
        from autocontext.agents.role_router import DEFAULT_ROUTING_TABLE, ProviderClass

        assert DEFAULT_ROUTING_TABLE["analyst"][0] == ProviderClass.MID_TIER

    def test_architect_prefers_frontier(self) -> None:
        from autocontext.agents.role_router import DEFAULT_ROUTING_TABLE, ProviderClass

        assert DEFAULT_ROUTING_TABLE["architect"][0] == ProviderClass.FRONTIER

    def test_translator_prefers_fast(self) -> None:
        from autocontext.agents.role_router import DEFAULT_ROUTING_TABLE, ProviderClass

        assert DEFAULT_ROUTING_TABLE["translator"][0] == ProviderClass.FAST

    def test_curator_prefers_fast(self) -> None:
        from autocontext.agents.role_router import DEFAULT_ROUTING_TABLE, ProviderClass

        assert DEFAULT_ROUTING_TABLE["curator"][0] == ProviderClass.FAST


# ---------------------------------------------------------------------------
# TestRoleRouter — basic routing
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> MagicMock:
    s = MagicMock()
    s.role_routing = overrides.get("role_routing", "auto")
    s.agent_provider = overrides.get("agent_provider", "anthropic")
    s.competitor_provider = overrides.get("competitor_provider", "")
    s.analyst_provider = overrides.get("analyst_provider", "")
    s.coach_provider = overrides.get("coach_provider", "")
    s.architect_provider = overrides.get("architect_provider", "")
    s.model_competitor = overrides.get("model_competitor", "claude-sonnet-4-5-20250929")
    s.model_analyst = overrides.get("model_analyst", "claude-sonnet-4-5-20250929")
    s.model_coach = overrides.get("model_coach", "claude-opus-4-6")
    s.model_architect = overrides.get("model_architect", "claude-opus-4-6")
    s.model_translator = overrides.get("model_translator", "claude-sonnet-4-5-20250929")
    s.model_curator = overrides.get("model_curator", "claude-opus-4-6")
    s.tier_haiku_model = "claude-haiku-4-5-20251001"
    s.tier_sonnet_model = "claude-sonnet-4-5-20250929"
    s.tier_opus_model = "claude-opus-4-6"
    s.mlx_model_path = overrides.get("mlx_model_path", "/tmp/distilled-model")
    return s


class TestRoleRouterAutoMode:
    def test_auto_routes_competitor_to_frontier(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("competitor")
        assert cfg.provider_class == ProviderClass.FRONTIER

    def test_auto_routes_analyst_to_mid_tier(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("analyst")
        assert cfg.provider_class == ProviderClass.MID_TIER

    def test_auto_routes_architect_to_frontier(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("architect")
        assert cfg.provider_class == ProviderClass.FRONTIER

    def test_auto_routes_translator_to_fast(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("translator")
        assert cfg.provider_class == ProviderClass.FAST

    def test_auto_routes_curator_to_fast(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("curator")
        assert cfg.provider_class == ProviderClass.FAST

    def test_auto_routes_coach_to_mid_tier(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("coach")
        assert cfg.provider_class == ProviderClass.MID_TIER

    def test_auto_returns_correct_model_for_frontier(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("competitor")
        assert cfg.model == "claude-opus-4-6"

    def test_auto_returns_correct_model_for_mid_tier(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("analyst")
        assert cfg.model == "claude-sonnet-4-5-20250929"

    def test_auto_returns_correct_model_for_fast(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("translator")
        assert cfg.model == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# TestRoleRouter — explicit overrides take precedence
# ---------------------------------------------------------------------------


class TestRoleRouterExplicitOverrides:
    def test_explicit_provider_overrides_auto(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings(competitor_provider="mlx"))
        cfg = router.route("competitor")
        assert cfg.provider_type == "mlx"

    def test_explicit_provider_sets_local_class(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings(analyst_provider="mlx"))
        cfg = router.route("analyst")
        assert cfg.provider_class == ProviderClass.LOCAL

    def test_explicit_openclaw_provider(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings(competitor_provider="openclaw"))
        cfg = router.route("competitor")
        assert cfg.provider_type == "openclaw"


# ---------------------------------------------------------------------------
# TestRoleRouter — disabled mode
# ---------------------------------------------------------------------------


class TestRoleRouterDisabledMode:
    def test_disabled_returns_default_provider(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings(role_routing="off"))
        cfg = router.route("competitor")
        assert cfg.provider_type == "anthropic"

    def test_disabled_uses_configured_model(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings(role_routing="off"))
        cfg = router.route("competitor")
        assert cfg.model == "claude-sonnet-4-5-20250929"


# ---------------------------------------------------------------------------
# TestRoleRouter — local artifact awareness
# ---------------------------------------------------------------------------


class TestRoleRouterLocalArtifacts:
    def test_competitor_uses_local_when_available(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter, RoutingContext

        router = RoleRouter(_settings())
        ctx = RoutingContext(
            available_local_models=["distilled_grid_ctf"],
            scenario_name="grid_ctf",
        )
        cfg = router.route("competitor", context=ctx)
        assert cfg.provider_class == ProviderClass.LOCAL

    def test_translator_uses_local_when_available(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter, RoutingContext

        router = RoleRouter(_settings())
        ctx = RoutingContext(
            available_local_models=["/tmp/distilled-model"],
            scenario_name="grid_ctf",
        )
        cfg = router.route("translator", context=ctx)
        assert cfg.provider_class == ProviderClass.LOCAL

    def test_analyst_uses_local_when_available(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter, RoutingContext

        router = RoleRouter(_settings())
        ctx = RoutingContext(available_local_models=["model1"])
        cfg = router.route("analyst", context=ctx)
        assert cfg.provider_class == ProviderClass.LOCAL

    def test_architect_ignores_local_models(self) -> None:
        """Architect needs frontier-class reasoning, should not be demoted to local."""
        from autocontext.agents.role_router import ProviderClass, RoleRouter, RoutingContext

        router = RoleRouter(_settings())
        ctx = RoutingContext(available_local_models=["model1"])
        cfg = router.route("architect", context=ctx)
        assert cfg.provider_class == ProviderClass.FRONTIER


# ---------------------------------------------------------------------------
# TestRoleRouter — custom routing table
# ---------------------------------------------------------------------------


class TestRoleRouterCustomTable:
    def test_custom_table_overrides_default(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        custom = {"competitor": [ProviderClass.FAST], "analyst": [ProviderClass.FRONTIER]}
        router = RoleRouter(_settings(), routing_table=custom)

        cfg = router.route("competitor")
        assert cfg.provider_class == ProviderClass.FAST

        cfg = router.route("analyst")
        assert cfg.provider_class == ProviderClass.FRONTIER

    def test_unknown_role_defaults_to_mid_tier(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("unknown_role")
        assert cfg.provider_class == ProviderClass.MID_TIER


# ---------------------------------------------------------------------------
# TestCostEstimation
# ---------------------------------------------------------------------------


class TestCostEstimation:
    def test_cost_for_frontier(self) -> None:
        from autocontext.agents.role_router import ProviderClass, RoleRouter

        router = RoleRouter(_settings())
        cfg = router.route("architect")
        assert cfg.provider_class == ProviderClass.FRONTIER
        assert cfg.estimated_cost_per_1k_tokens > 0

    def test_cost_for_local_is_zero(self) -> None:
        from autocontext.agents.role_router import RoleRouter, RoutingContext

        router = RoleRouter(_settings())
        ctx = RoutingContext(available_local_models=["m1"])
        cfg = router.route("analyst", context=ctx)
        assert cfg.estimated_cost_per_1k_tokens == 0.0

    def test_estimate_run_cost(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings())
        estimate = router.estimate_run_cost()
        assert "total_per_1k_tokens" in estimate
        assert "roles" in estimate
        assert "savings_vs_all_frontier" in estimate
        assert estimate["total_per_1k_tokens"] >= 0

    def test_estimate_run_cost_savings(self) -> None:
        from autocontext.agents.role_router import RoleRouter

        router = RoleRouter(_settings())
        estimate = router.estimate_run_cost()
        # With auto routing, mid-tier and fast roles should save vs all-frontier
        assert estimate["savings_vs_all_frontier"] >= 0

    def test_estimate_run_cost_with_local(self) -> None:
        from autocontext.agents.role_router import RoleRouter, RoutingContext

        router = RoleRouter(_settings())
        ctx = RoutingContext(available_local_models=["m1"])
        estimate = router.estimate_run_cost(context=ctx)
        # Local models reduce cost further
        estimate_no_local = router.estimate_run_cost()
        assert estimate["total_per_1k_tokens"] <= estimate_no_local["total_per_1k_tokens"]


# ---------------------------------------------------------------------------
# TestSettings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_role_routing_setting(self) -> None:
        from autocontext.config.settings import AppSettings

        s = AppSettings()
        assert hasattr(s, "role_routing")
        assert s.role_routing == "off"  # off by default, opt-in

    def test_role_routing_auto(self) -> None:
        import os
        from unittest.mock import patch

        from autocontext.config.settings import load_settings

        with patch.dict(os.environ, {"AUTOCONTEXT_ROLE_ROUTING": "auto"}):
            s = load_settings()
        assert s.role_routing == "auto"
