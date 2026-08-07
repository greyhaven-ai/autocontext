from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


def _contract() -> dict[str, Any]:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "role-routing-contract.json"
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("role-routing contract must be a JSON object")
    return cast(dict[str, Any], payload)


def test_python_role_router_constants_match_shared_contract() -> None:
    from autocontext.agents import role_router
    from autocontext.agents.role_router import ProviderClass

    contract = _contract()

    assert [provider_class.value for provider_class in ProviderClass] == contract["provider_classes"]
    assert {
        role: [provider_class.value for provider_class in preferences]
        for role, preferences in role_router.DEFAULT_ROUTING_TABLE.items()
    } == contract["default_routing_table"]
    assert sorted(role_router._LOCAL_ELIGIBLE_ROLES) == sorted(contract["local_eligible_roles"])  # noqa: SLF001
    assert {
        provider_class.value: cost
        for provider_class, cost in role_router._COST_TABLE.items()  # noqa: SLF001
    } == contract["cost_per_1k_tokens"]
    assert {
        provider: provider_class.value
        for provider, provider_class in role_router._EXPLICIT_PROVIDER_CLASS.items()  # noqa: SLF001
    } == contract["explicit_provider_classes"]


def test_python_supported_provider_types_match_contract() -> None:
    """An unlisted Python provider is drift; a listed-but-missing one is a stale contract."""
    contract = _contract()
    declared = {name for name, entry in contract["supported_provider_types"].items() if "python" in entry["packages"]}
    from autocontext.providers import registry

    actual = registry.supported_provider_types()
    assert actual == declared


def test_single_package_provider_entries_carry_a_reason() -> None:
    """A one-language provider is allowed, but only as a deliberate, explained choice."""
    for name, entry in _contract()["supported_provider_types"].items():
        if len(entry["packages"]) < 2:
            assert entry.get("reason"), f"{name} is single-package but has no reason"
