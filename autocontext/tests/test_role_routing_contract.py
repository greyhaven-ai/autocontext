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


def test_contract_is_internally_consistent() -> None:
    """Catches contract edits that codegen would happily propagate as nonsense.

    These invariants cannot be satisfied by construction the way the old
    constants-match-contract assertions could, because both sides of each
    comparison come from different sections of the contract.
    """
    contract = _contract()
    classes = set(contract["provider_classes"])

    unknown_cost_classes = set(contract["cost_per_1k_tokens"]) - classes
    assert not unknown_cost_classes, f"cost table names unknown classes: {sorted(unknown_cost_classes)}"

    for role, preferences in contract["default_routing_table"].items():
        unknown = set(preferences) - classes
        assert not unknown, f"role {role!r} prefers unknown classes: {sorted(unknown)}"

    routed_roles = set(contract["default_routing_table"])
    unknown_local = set(contract["local_eligible_roles"]) - routed_roles
    assert not unknown_local, f"local_eligible_roles names unrouted roles: {sorted(unknown_local)}"

    unknown_explicit = set(contract["explicit_provider_classes"].values()) - classes
    assert not unknown_explicit, f"explicit_provider_classes names unknown classes: {sorted(unknown_explicit)}"

    missing_role_fields = routed_roles - set(contract["role_model_fields"])
    assert not missing_role_fields, f"roles with no model field mapping: {sorted(missing_role_fields)}"

    missing_class_fields = classes - set(contract["class_model_fields"]) - {"code_policy"}
    assert not missing_class_fields, f"classes with no model field mapping: {sorted(missing_class_fields)}"


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
