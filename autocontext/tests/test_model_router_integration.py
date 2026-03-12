"""Integration tests for model router wiring into orchestrator."""
from __future__ import annotations

from pathlib import Path

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.model_router import ModelRouter, TierConfig
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.config.settings import AppSettings
from autocontext.storage.artifacts import ArtifactStore


def test_settings_has_tier_fields() -> None:
    settings = AppSettings()
    assert hasattr(settings, "tier_routing_enabled")
    assert settings.tier_routing_enabled is False


def test_settings_tier_models_configurable() -> None:
    settings = AppSettings(tier_routing_enabled=True, tier_haiku_model="custom-haiku")
    assert settings.tier_haiku_model == "custom-haiku"


def test_settings_tier_defaults() -> None:
    settings = AppSettings()
    assert settings.tier_haiku_model == "claude-haiku-4-5-20251001"
    assert settings.tier_sonnet_model == "claude-sonnet-4-5-20250929"
    assert settings.tier_opus_model == "claude-opus-4-6"
    assert settings.tier_competitor_haiku_max_gen == 3
    assert settings.tier_harness_aware_enabled is False
    assert settings.tier_harness_coverage_demotion_threshold == 0.8


def test_router_from_settings() -> None:
    """ModelRouter can be constructed from AppSettings fields."""
    settings = AppSettings(
        tier_routing_enabled=True,
        tier_harness_aware_enabled=True,
        tier_harness_coverage_demotion_threshold=0.7,
    )
    config = TierConfig(
        enabled=settings.tier_routing_enabled,
        tier_haiku_model=settings.tier_haiku_model,
        tier_sonnet_model=settings.tier_sonnet_model,
        tier_opus_model=settings.tier_opus_model,
        competitor_haiku_max_gen=settings.tier_competitor_haiku_max_gen,
        harness_aware_tiering_enabled=settings.tier_harness_aware_enabled,
        harness_coverage_demotion_threshold=settings.tier_harness_coverage_demotion_threshold,
    )
    router = ModelRouter(config)
    model = router.select("competitor", generation=1, retry_count=0, is_plateau=False)
    assert model == settings.tier_haiku_model


def test_orchestrator_resolve_model_uses_harness_coverage(tmp_path: Path) -> None:
    knowledge_root = tmp_path / "knowledge"
    harness_dir = knowledge_root / "grid_ctf" / "harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "preflight_synthesized.py").write_text(
        """
def validate_strategy(strategy, scenario):
    return True, []

def enumerate_legal_actions(state):
    return []

def parse_game_state(payload):
    return payload

def is_legal_action(state, action):
    return True
""".strip()
        + "\n",
        encoding="utf-8",
    )

    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=knowledge_root,
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    settings = AppSettings(
        tier_routing_enabled=True,
        tier_harness_aware_enabled=True,
        knowledge_root=knowledge_root,
        runs_root=tmp_path / "runs",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    orch = AgentOrchestrator(client=DeterministicDevClient(), settings=settings, artifacts=artifacts)

    model = orch.resolve_model("competitor", generation=10, scenario_name="grid_ctf")
    assert model == settings.tier_haiku_model
