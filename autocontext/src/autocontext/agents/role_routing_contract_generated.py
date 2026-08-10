"""AUTO-GENERATED from docs/role-routing-contract.json — DO NOT EDIT.

Regenerate with: node ts/scripts/generate-role-routing-contract.mjs
CI gate:         node ts/scripts/generate-role-routing-contract.mjs --check
"""

from __future__ import annotations

from typing import Final

PROVIDER_CLASSES: Final[tuple[str, ...]] = ("frontier", "mid_tier", "fast", "local", "code_policy")

MODE_VALUES: Final[tuple[str, ...]] = ("off", "auto")

COST_PER_1K_TOKENS: Final[dict[str, float]] = {
    "fast": 0.001,
    "frontier": 0.015,
    "local": 0.0,
    "mid_tier": 0.003,
}

DEFAULT_ROUTING_TABLE: Final[dict[str, list[str]]] = {
    "analyst": ["mid_tier", "local"],
    "architect": ["frontier"],
    "coach": ["mid_tier", "local"],
    "competitor": ["frontier", "local"],
    "curator": ["fast"],
    "translator": ["fast", "local"],
}

LOCAL_ELIGIBLE_ROLES: Final[frozenset[str]] = frozenset(["competitor", "analyst", "coach", "translator"])

EXPLICIT_PROVIDER_CLASSES: Final[dict[str, str]] = {
    "agent_sdk": "frontier",
    "anthropic": "frontier",
    "deterministic": "fast",
    "mlx": "local",
    "ollama": "mid_tier",
    "openai": "mid_tier",
    "openai-compatible": "mid_tier",
    "openclaw": "frontier",
    "vllm": "mid_tier",
}

# Python settings key -> the TypeScript field holding the same value. Python reads
# the keys; TypeScript reads the values. Declared once so neither package can add a
# routing-relevant setting the other never learns about.
SETTINGS_KEYS: Final[dict[str, str]] = {
    "agent_provider": "agentProvider",
    "analyst_provider": "analystProvider",
    "architect_provider": "architectProvider",
    "coach_provider": "coachProvider",
    "competitor_provider": "competitorProvider",
    "local_model": "localModel",
    "mlx_model_path": "mlxModelPath",
    "model_analyst": "modelAnalyst",
    "model_architect": "modelArchitect",
    "model_coach": "modelCoach",
    "model_competitor": "modelCompetitor",
    "model_curator": "modelCurator",
    "model_translator": "modelTranslator",
    "role_routing": "roleRouting",
    "tier_haiku_model": "tierHaikuModel",
    "tier_opus_model": "tierOpusModel",
    "tier_sonnet_model": "tierSonnetModel",
}
