"""Stage 1 of ERP-67 — structural role-message isolation.

`build_prompt_bundle` exposes, per role, a (system, untrusted_reference, flat)
split via an optional `prompt_parts_sink`, WITHOUT changing the flat prompt the
transport still sends. This is the behaviour-preserving separation of concerns
that Stage 2 will thread through the LLM message roles.

Invariants asserted here:
- `flat` is byte-identical to the corresponding `PromptBundle` role string
  (nothing about today's behaviour changes).
- The attacker-influenceable fields (playbook, coach hints, dead-ends) appear
  in `untrusted_reference` and NOT in `system`.
- The trust-boundary guardrail and the role task live in `system`.
- Only the competitor carries coach hints; other roles do not.
"""

from __future__ import annotations

import autocontext.agents  # noqa: F401  # break circular import with prompts.templates
from autocontext.extensions import HookBus, HookEvents, HookResult
from autocontext.prompts.templates import (
    PromptPartsBundle,
    RolePromptParts,
    build_prompt_bundle,
)
from autocontext.scenarios.base import Observation

_PLAYBOOK = "PLAYBOOK-SENTINEL-1234 keep aggression high"
_HINTS = "HINTS-SENTINEL-5678 try corner play"
_DEAD_ENDS = "DEADEND-SENTINEL-9012 never rush the center"


def _observation() -> Observation:
    return Observation(narrative="test narrative", state={}, constraints=[])


def _build() -> tuple[PromptPartsBundle, object]:
    captured: dict[str, PromptPartsBundle] = {}
    bundle = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=_observation(),
        current_playbook=_PLAYBOOK,
        available_tools="tools",
        coach_competitor_hints=_HINTS,
        dead_ends=_DEAD_ENDS,
        prompt_parts_sink=lambda parts: captured.__setitem__("parts", parts),
    )
    return captured["parts"], bundle


def test_sink_receives_parts_for_every_role() -> None:
    parts, _ = _build()
    assert isinstance(parts, PromptPartsBundle)
    for role in (parts.competitor, parts.analyst, parts.coach, parts.architect):
        assert isinstance(role, RolePromptParts)


def test_flat_is_byte_identical_to_prompt_bundle() -> None:
    parts, bundle = _build()
    assert parts.competitor.flat == bundle.competitor
    assert parts.analyst.flat == bundle.analyst
    assert parts.coach.flat == bundle.coach
    assert parts.architect.flat == bundle.architect


def test_untrusted_fields_are_isolated_from_system() -> None:
    parts, _ = _build()
    for role in (parts.competitor, parts.analyst, parts.coach, parts.architect):
        # Playbook and dead-ends are untrusted for every role.
        assert _PLAYBOOK in role.untrusted_reference
        assert _DEAD_ENDS in role.untrusted_reference
        assert _PLAYBOOK not in role.system
        assert _DEAD_ENDS not in role.system


def test_only_competitor_carries_coach_hints() -> None:
    parts, _ = _build()
    assert _HINTS in parts.competitor.untrusted_reference
    assert _HINTS not in parts.competitor.system
    # Other roles never receive the competitor hints at all.
    for role in (parts.analyst, parts.coach, parts.architect):
        assert _HINTS not in role.untrusted_reference
        assert _HINTS not in role.system


def test_system_carries_guardrail_and_task() -> None:
    parts, _ = _build()
    for role in (parts.competitor, parts.analyst, parts.coach, parts.architect):
        assert "trust boundary" in role.system.lower()
    # Role-specific task text lands in system, not the untrusted region.
    assert "consolidated playbook" in parts.coach.system.lower()
    assert "consolidated playbook" not in parts.coach.untrusted_reference.lower()


def test_sink_is_optional() -> None:
    # Omitting the sink must not raise and must still return the flat bundle.
    bundle = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=_observation(),
        current_playbook=_PLAYBOOK,
        available_tools="tools",
    )
    assert _PLAYBOOK in bundle.competitor


def test_parts_are_isolation_safe_without_a_hook() -> None:
    parts, _ = _build()
    for role in (parts.competitor, parts.analyst, parts.coach, parts.architect):
        assert role.isolation_safe is True


def _redacting_hook_bus() -> HookBus:
    """A CONTEXT hook that redacts every role's final prompt — the reviewer's
    scenario for PR #1201: the split must not leak content the hook removed."""
    bus = HookBus()

    def _redact(event: object) -> HookResult:
        redacted = {role: "REDACTED" for role in ("competitor", "analyst", "coach", "architect")}
        return HookResult(payload={"roles": redacted})

    bus.on(HookEvents.CONTEXT, _redact)
    return bus


def test_hook_rewritten_roles_do_not_leak_redacted_content() -> None:
    captured: dict[str, PromptPartsBundle] = {}
    build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=_observation(),
        current_playbook=_PLAYBOOK,
        available_tools="tools",
        coach_competitor_hints=_HINTS,
        dead_ends=_DEAD_ENDS,
        hook_bus=_redacting_hook_bus(),
        prompt_parts_sink=lambda parts: captured.__setitem__("parts", parts),
    )
    parts = captured["parts"]
    for role in (parts.competitor, parts.analyst, parts.coach, parts.architect):
        # Hook redacted the prompt: the split is untrustworthy and must fall back.
        assert role.isolation_safe is False
        assert role.flat == "REDACTED"
        assert role.system == ""
        assert role.untrusted_reference == "REDACTED"
        # Content the hook removed must NOT reappear via the split.
        assert _PLAYBOOK not in role.system
        assert _PLAYBOOK not in role.untrusted_reference
        assert _DEAD_ENDS not in role.untrusted_reference
        assert _HINTS not in role.untrusted_reference
