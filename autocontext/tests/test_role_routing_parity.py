"""AC-910 step 1: cross-language routing parity.

Replays docs/role-routing-parity-fixtures.json through the Python RoleRouter.
ts/tests/role-routing-parity.test.ts replays the identical file through the
TypeScript routeRoleProvider. Both must agree exactly.

To add a scenario group: add it to the fixture, then add its name to
ROUTE_GROUPS here and to ROUTE_GROUPS in the TypeScript replay.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "docs" / "role-routing-parity-fixtures.json"

# Fixture groups whose cases are single route() calls compared field by field.
# Groups with a different shape (cost estimation) get their own replay test.
ROUTE_GROUPS: tuple[str, ...] = (
    "auto_mode",
    "explicit_override",
    "routing_off",
    "local_artifacts",
)

# Every settings key a fixture case may set, with the value used when a case omits it.
# Mirrors the TypeScript baseSettings() helper so both languages start from identical state.
# The placeholder model names are deliberately not real Claude ids: they make it obvious
# which settings field a returned model came from.
_SETTINGS_DEFAULTS: dict[str, str] = {
    "role_routing": "auto",
    "agent_provider": "anthropic",
    "competitor_provider": "",
    "analyst_provider": "",
    "coach_provider": "",
    "architect_provider": "",
    "model_competitor": "competitor-role-model",
    "model_analyst": "analyst-role-model",
    "model_coach": "coach-role-model",
    "model_architect": "architect-role-model",
    "model_curator": "curator-role-model",
    "model_translator": "translator-role-model",
    "tier_opus_model": "opus-tier-model",
    "tier_sonnet_model": "sonnet-tier-model",
    "tier_haiku_model": "haiku-tier-model",
    "mlx_model_path": "/models/default-local",
}

# The fixture uses the TypeScript spelling for context fields so one file drives
# both languages. Python's RoutingContext uses snake_case.
_CONTEXT_KEY_MAP: dict[str, str] = {
    "availableLocalModels": "available_local_models",
}


def _load() -> dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("parity fixture must be a JSON object")
    return cast(dict[str, Any], payload)


def _cases(group: str) -> list[tuple[str, dict[str, Any]]]:
    fixtures = _load()["fixtures"]
    if group not in fixtures:
        raise AssertionError(f"fixture group {group!r} missing")
    return sorted(fixtures[group].items())


def _route_cases() -> list[tuple[str, str, dict[str, Any]]]:
    return [(group, name, case) for group in ROUTE_GROUPS for name, case in _cases(group)]


def _settings_from_fixture(overrides: dict[str, Any]) -> MagicMock:
    """Build a settings double from fixture overrides layered on shared defaults."""
    unknown = set(overrides) - set(_SETTINGS_DEFAULTS)
    if unknown:
        raise AssertionError(f"fixture sets unknown settings keys: {sorted(unknown)}")
    settings = MagicMock()
    for key, default in _SETTINGS_DEFAULTS.items():
        setattr(settings, key, overrides.get(key, default))
    return settings


def _context_from_fixture(raw: dict[str, Any]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for key, value in raw.items():
        mapped = _CONTEXT_KEY_MAP.get(key)
        if mapped is None:
            raise AssertionError(f"fixture sets unknown context key: {key}")
        converted[mapped] = value
    return converted


def _assert_route_matches(result: Any, expected: dict[str, Any]) -> None:
    assert result.provider_type == expected["provider_type"]
    assert result.model == expected["model"]
    assert result.provider_class.value == expected["provider_class"]
    assert result.estimated_cost_per_1k_tokens == pytest.approx(expected["cost_per_1k_tokens"])


@pytest.mark.parametrize("group,case_name,case", _route_cases())
def test_route_parity(group: str, case_name: str, case: dict[str, Any]) -> None:
    """Every registered group's cases must match the values TypeScript produces."""
    from autocontext.agents.role_router import RoleRouter, RoutingContext

    del group, case_name  # surfaced by the parametrize id on failure
    router = RoleRouter(_settings_from_fixture(case.get("settings", {})))
    context = RoutingContext(**_context_from_fixture(case.get("context", {})))
    _assert_route_matches(router.route(case["role"], context=context), case["expected"])
