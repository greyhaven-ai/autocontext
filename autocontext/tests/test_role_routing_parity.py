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


@pytest.mark.parametrize("case_name,case", _cases("cost_estimation"))
def test_cost_estimation_parity(case_name: str, case: dict[str, Any]) -> None:
    """Cost totals must match TypeScript's estimateRoleRoutingCost()."""
    from autocontext.agents.role_router import RoleRouter, RoutingContext

    del case_name
    router = RoleRouter(_settings_from_fixture(case.get("settings", {})))
    context = RoutingContext(**_context_from_fixture(case.get("context", {})))
    estimate = router.estimate_run_cost(context=context)

    expected = case["expected"]
    for key in ("total_per_1k_tokens", "all_frontier_per_1k_tokens", "savings_vs_all_frontier"):
        assert estimate[key] == pytest.approx(expected[key]), key


# Every group the fixture is expected to contain, including groups that are not in
# ROUTE_GROUPS (cost_estimation has its own replay; a "divergent" group, if Task 2
# created one, is data only). If you add a group, add it here too, or the guard
# stops guarding.
_EXPECTED_GROUPS = {
    "auto_mode",
    "explicit_override",
    "routing_off",
    "local_artifacts",
    "cost_estimation",
}


def test_every_expected_fixture_group_is_present() -> None:
    """A deleted group would otherwise make both suites pass with less coverage."""
    assert set(_load()["fixtures"]) == _EXPECTED_GROUPS


def test_no_expected_group_is_empty() -> None:
    """Emptying a group is the silent failure the present-check does not catch.

    `"auto_mode": {}` keeps the key, so the presence check still passes, but
    `_cases()` returns [] and pytest reports an empty parametrize list as
    "1 skipped" with exit code 0: a green CI run that asserted nothing.
    The TypeScript replay already guards this per group via its
    "has cases to replay" test; this is the Python counterpart.
    """
    fixtures = _load()["fixtures"]
    empty = sorted(name for name in _EXPECTED_GROUPS if not fixtures.get(name))
    assert not empty, f"fixture groups present but empty: {empty}"


def test_known_divergences_are_fully_described() -> None:
    """A divergence recorded without a resolution is an untracked bug."""
    divergences = _load()["known_divergences"]
    assert divergences, "expected at least the mixed-case provider divergence from Task 3"
    required = {"case", "python", "typescript", "reason", "resolution"}
    for entry in divergences:
        missing = required - set(entry)
        assert not missing, f"divergence entry missing keys: {sorted(missing)}"


def test_fixture_typo_guards_actually_raise() -> None:
    """The harness's own typo guards are otherwise never executed.

    `_settings_from_fixture` and `_context_from_fixture` raise on unknown keys
    so that a misspelled fixture key fails loudly. If those guards were broken,
    a typo would be silently ignored, the case would run against default
    settings, and it could pass while testing something other than what it
    claims. That is the same silent-green failure this whole fixture exists to
    prevent, so the guards themselves get a test.
    """
    with pytest.raises(AssertionError, match="unknown settings keys"):
        _settings_from_fixture({"not_a_real_setting": "x"})
    with pytest.raises(AssertionError, match="unknown context key"):
        _context_from_fixture({"notARealContextKey": []})
