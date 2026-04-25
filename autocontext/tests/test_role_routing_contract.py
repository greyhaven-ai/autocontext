from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _contract() -> dict[str, Any]:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "role-routing-contract.json"
    return json.loads(contract_path.read_text(encoding="utf-8"))


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
