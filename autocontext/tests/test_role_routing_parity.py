"""AC-910 step 1: cross-language routing parity.

Replays docs/role-routing-parity-fixtures.json through the Python RoleRouter.
ts/tests/role-routing-parity.test.ts replays the identical file through the
TypeScript routeRoleProvider. Both must agree exactly.

To add a scenario: add it to the fixture, then add its case id to
_EXPECTED_CASE_IDS here and EXPECTED_CASE_IDS in the TypeScript replay.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "docs" / "role-routing-parity-fixtures.json"

# The fixture is the behavioral contract, but the expected case ids live outside
# it so deleting or replacing a critical scenario cannot silently reduce coverage.
_EXPECTED_CASE_IDS: dict[str, frozenset[str]] = {
    "auto_mode": frozenset(
        {
            "competitor_frontier",
            "analyst_mid_tier",
            "coach_mid_tier",
            "architect_frontier_when_no_artifact_is_available",
            "curator_fast",
            "translator_fast",
            "unknown_role_falls_back_to_mid_tier",
            "self_hosted_default_provider_is_frontier_via_table",
        },
    ),
    "explicit_override": frozenset(
        {
            "competitor_override_to_ollama_is_mid_tier",
            "architect_override_to_vllm_is_mid_tier",
            "unknown_override_provider_defaults_to_frontier",
            "override_to_mlx_uses_mlx_model_path",
            "override_wins_over_role_routing_off",
        },
    ),
    "routing_off": frozenset(
        {
            "off_uses_default_provider_and_role_model",
            "off_with_ollama_default_is_mid_tier",
            "off_with_unknown_default_provider_is_mid_tier",
            "off_uses_coach_role_model",
            "off_uses_curator_role_model",
            "off_uses_translator_role_model",
            "off_with_mlx_default_uses_mlx_model_path",
        },
    ),
    "local_artifacts": frozenset(
        {
            "eligible_role_prefers_local_artifact",
            "architect_uses_local_artifact",
            "curator_uses_local_artifact",
            "local_artifact_ignored_when_routing_off",
        },
    ),
    "cost_estimation": frozenset(
        {
            "default_auto_mode_totals",
            "with_local_artifacts_totals",
        },
    ),
}

_EXPECTED_DIVERGENCE_CASE_IDS = {
    "explicit_override.mixed_case_provider_name",
    "explicit_override.whitespace_only_provider_name",
    "routing_off.unknown_role_model",
    # AC-919 item 2. Every other fixture case supplies all 16 settings fields
    # non-empty, so nothing reached the unset/empty layer -- the exact layer
    # AC-912 rewrites. These five pin its before-state so that rewrite can be
    # diffed rather than trusted.
    "unset_settings.agent_provider_empty",
    "unset_settings.blank_local_artifact",
    "unset_settings.mlx_model_path_empty",
    "unset_settings.role_model_empty_routing_off",
    "unset_settings.tier_model_empty",
}

# Fixture groups whose cases are single route() calls compared field by field.
# Groups with a different shape (cost estimation) get their own replay test.
ROUTE_GROUPS: tuple[str, ...] = tuple(group for group in _EXPECTED_CASE_IDS if group != "cost_estimation")
_EXPECTED_GROUPS = set(_EXPECTED_CASE_IDS)
_EXPECTED_ASSIGNMENT_KEYS = {
    "provider_type",
    "model",
    "provider_class",
    "cost_per_1k_tokens",
}

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
    # AC-912. Empty is the shipped default; a non-empty value would fill every
    # unset role/tier slot for non-anthropic providers, which is a different
    # contract than this fixture pins.
    "local_model": "",
    # AC-911. Empty means capability is inferred from the transport, which is
    # the state every case in this fixture was recorded under.
    "provider_capability": "",
    "provider_hosting": "",
    "competitor_provider_capability": "",
    "analyst_provider_capability": "",
    "coach_provider_capability": "",
    "architect_provider_capability": "",
    "competitor_provider_hosting": "",
    "analyst_provider_hosting": "",
    "coach_provider_hosting": "",
    "architect_provider_hosting": "",
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


def _divergence_cases() -> list[tuple[str, dict[str, Any]]]:
    return sorted((entry["case"], entry) for entry in _load()["known_divergences"])


def _settings_from_fixture(overrides: dict[str, Any]) -> MagicMock:
    """Build a settings double from fixture overrides layered on shared defaults.

    Specced against ``_SETTINGS_DEFAULTS`` (AC-919 item 8). A bare MagicMock
    returns a truthy auto-Mock for any attribute a refactor newly reads, so
    Python silently opted into new behavior while TypeScript's literal
    baseSettings() opted out -- AC-912 hit exactly that, resolving a role model
    to ``mock.local_model.strip()``. With a spec, an unlisted field raises
    AttributeError and names itself instead.

    ``model_fields_set`` reports every field the double defines, because the
    fixture specifies a concrete value for all of them: within this contract
    every setting is configured, so provider-default resolution (AC-912) must
    not treat any of them as untouched.
    """
    unknown = set(overrides) - set(_SETTINGS_DEFAULTS)
    if unknown:
        raise AssertionError(f"fixture sets unknown settings keys: {sorted(unknown)}")
    settings = MagicMock(spec=[*_SETTINGS_DEFAULTS, "model_fields_set"])
    settings.model_fields_set = frozenset(_SETTINGS_DEFAULTS)
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


@pytest.mark.parametrize("case_name,case", _divergence_cases())
def test_python_known_divergence_output(case_name: str, case: dict[str, Any]) -> None:
    """Known disagreements remain executable until AC-911 resolves them."""
    from autocontext.agents.role_router import RoleRouter, RoutingContext

    del case_name
    router = RoleRouter(_settings_from_fixture(case.get("settings", {})))
    context = RoutingContext(**_context_from_fixture(case.get("context", {})))
    _assert_route_matches(router.route(case["role"], context=context), case["python"])
    assert case["python"] != case["typescript"]


def test_every_expected_fixture_group_is_present() -> None:
    """The external inventory pins both fixture groups and replay registration."""
    assert set(_load()["fixtures"]) == _EXPECTED_GROUPS
    assert set(ROUTE_GROUPS) | {"cost_estimation"} == _EXPECTED_GROUPS


def test_every_expected_fixture_case_is_present() -> None:
    """Deleting or replacing one scenario must not silently reduce coverage."""
    fixtures = _load()["fixtures"]
    actual = {group: set(cases) for group, cases in fixtures.items()}
    expected = {group: set(case_ids) for group, case_ids in _EXPECTED_CASE_IDS.items()}
    assert actual == expected


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
    """Divergences must have runnable inputs, exact outputs, and a resolution."""
    divergences = _load()["known_divergences"]
    case_ids = [entry["case"] for entry in divergences]
    assert len(case_ids) == len(set(case_ids)), "duplicate known-divergence case ids"
    assert set(case_ids) == _EXPECTED_DIVERGENCE_CASE_IDS

    required = {
        "case",
        "role",
        "settings",
        "context",
        "python",
        "typescript",
        "reason",
        "resolution",
    }
    for entry in divergences:
        missing = required - set(entry)
        assert not missing, f"divergence entry missing keys: {sorted(missing)}"
        assert set(entry["python"]) == _EXPECTED_ASSIGNMENT_KEYS
        assert set(entry["typescript"]) == _EXPECTED_ASSIGNMENT_KEYS
        assert entry["python"] != entry["typescript"]
        assert entry["reason"].strip()
        assert entry["resolution"].strip()


def test_settings_defaults_cover_every_contract_settings_key() -> None:
    """AC-911: the two replays' settings surfaces are declared once, in the contract.

    Exact-set equality, not a subset check: a field this replay stops supplying is
    as much a coverage hole as one it never supplied. AC-912 added ``local_model``
    here and not to the TypeScript replay, and nothing failed; this is the
    assertion that would have caught it.
    """
    from autocontext.agents.role_routing_contract_generated import SETTINGS_KEYS

    assert set(_SETTINGS_DEFAULTS) == set(SETTINGS_KEYS)


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
