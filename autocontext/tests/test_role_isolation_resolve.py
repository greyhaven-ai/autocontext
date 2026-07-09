"""ERP-67 — the shared capability-gated turn resolver used by both execution paths."""

from __future__ import annotations

from autocontext.agents.role_isolation import resolve_role_turn
from autocontext.prompts.templates import RolePromptParts


class _Capable:
    supports_structural_isolation = True


class _Incapable:
    supports_structural_isolation = False


def _parts(*, safe: bool = True) -> RolePromptParts:
    return RolePromptParts(
        system="SYS trusted",
        untrusted_reference="UNTRUSTED ref",
        flat="FLAT legacy",
        isolation_safe=safe,
    )


def test_capable_client_gets_isolated_turns() -> None:
    user, system = resolve_role_turn(_parts(), _Capable())
    assert user == "UNTRUSTED ref"
    assert system == "SYS trusted"


def test_incapable_client_falls_back_to_flat() -> None:
    user, system = resolve_role_turn(_parts(), _Incapable())
    assert user == "FLAT legacy"
    assert system == ""


def test_unsafe_split_falls_back_to_flat_even_for_capable_clients() -> None:
    user, system = resolve_role_turn(_parts(safe=False), _Capable())
    assert user == "FLAT legacy"
    assert system == ""


def test_suffix_rides_system_when_isolating_and_flat_when_falling_back() -> None:
    user, system = resolve_role_turn(_parts(), _Capable(), suffix=" +CADENCE")
    assert user == "UNTRUSTED ref"
    assert system == "SYS trusted +CADENCE"

    user2, system2 = resolve_role_turn(_parts(), _Incapable(), suffix=" +CADENCE")
    assert user2 == "FLAT legacy +CADENCE"
    assert system2 == ""


def test_missing_capability_attribute_is_treated_as_incapable() -> None:
    user, system = resolve_role_turn(_parts(), object())
    assert user == "FLAT legacy"
    assert system == ""
