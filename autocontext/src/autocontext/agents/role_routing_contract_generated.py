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
    "local": 0,
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
