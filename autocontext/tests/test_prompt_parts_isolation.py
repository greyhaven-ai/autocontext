"""Stage 1 of ERP-67 — structural role-message isolation.

`build_prompt_bundle` exposes, per role, a (system, untrusted_reference, flat)
split via an optional `prompt_parts_sink`, WITHOUT changing the flat prompt the
transport still sends. This is the behaviour-preserving separation of concerns
that Stage 2 will thread through the LLM message roles.

Invariants asserted here:
- `flat` is byte-identical to the corresponding `PromptBundle` role string
  (nothing about today's behaviour changes).
- The system turn is operator-authored ONLY. Every model/user/document/
  environment-derived component (playbook, hints, dead-ends, task observation,
  analyst output, lessons, tools, session reports, evidence, notebooks, etc.)
  appears in `untrusted_reference` and NOT in `system`.
- The trust-boundary guardrail, role task, and constraints live in `system`.
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


# ---------------------------------------------------------------------------
# Trust classification: ALL non-operator (model/user/document/env-derived)
# context must be in the untrusted turn, not just the ERP-59 fenced fields.
# Regression guard for the second-order-injection finding.
# ---------------------------------------------------------------------------

# Distinct per-component sentinels so a misclassified component is identifiable.
_SHARED_DERIVED = {
    "current_playbook": "S-PLAYBOOK",
    "operational_lessons": "S-LESSONS",  # coach-generated
    "recent_analysis": "S-ANALYSIS",  # analyst output
    "available_tools": "S-TOOLS",  # architect-generated
    "dead_ends": "S-DEADENDS",
    "session_reports": "S-SESSIONREPORTS",  # document/model-derived
    "environment_snapshot": "S-ENVSNAPSHOT",  # environment-derived
    "replay_narrative": "S-REPLAY",
    "score_trajectory": "S-TRAJECTORY",
    "experiment_log": "S-EXPERIMENTLOG",
}
# Scenario contract — trusted ONLY when the caller asserts it; the solve path
# generates these with an LLM, so they default to the untrusted turn.
_CONTRACT = {
    "scenario_rules": "CT-RULES",
    "strategy_interface": "CT-INTERFACE",
    "evaluation_criteria": "CT-CRITERIA",
}


def _build_classified(*, scenario_contract_trusted: bool = False) -> PromptPartsBundle:
    captured: dict[str, PromptPartsBundle] = {}
    build_prompt_bundle(
        scenario_rules=_CONTRACT["scenario_rules"],
        strategy_interface=_CONTRACT["strategy_interface"],
        evaluation_criteria=_CONTRACT["evaluation_criteria"],
        previous_summary="summary",
        observation=Observation(narrative="OBS-NARRATIVE derived", state={}, constraints=[]),
        current_playbook=_SHARED_DERIVED["current_playbook"],
        available_tools=_SHARED_DERIVED["available_tools"],
        operational_lessons=_SHARED_DERIVED["operational_lessons"],
        recent_analysis=_SHARED_DERIVED["recent_analysis"],
        dead_ends=_SHARED_DERIVED["dead_ends"],
        session_reports=_SHARED_DERIVED["session_reports"],
        environment_snapshot=_SHARED_DERIVED["environment_snapshot"],
        replay_narrative=_SHARED_DERIVED["replay_narrative"],
        score_trajectory=_SHARED_DERIVED["score_trajectory"],
        experiment_log=_SHARED_DERIVED["experiment_log"],
        architect_tool_usage_report="A-TOOLUSAGE",
        scout_mutation_guidance="SCOUT-GUIDANCE instruction: mutate toward corners",
        notebook_contexts={
            "competitor": "NB-COMPETITOR",
            "analyst": "NB-ANALYST",
            "coach": "NB-COACH",
            "architect": "NB-ARCHITECT",
        },
        scenario_contract_trusted=scenario_contract_trusted,
        prompt_parts_sink=lambda parts: captured.__setitem__("parts", parts),
    )
    return captured["parts"]


def test_all_derived_context_is_in_the_untrusted_turn_not_system() -> None:
    parts = _build_classified()
    for role in (parts.competitor, parts.analyst, parts.coach, parts.architect):
        # Every shared model/user/document/env-derived component: untrusted, never system.
        for sentinel in _SHARED_DERIVED.values():
            assert sentinel in role.untrusted_reference, f"{sentinel} missing from untrusted turn"
            assert sentinel not in role.system, f"{sentinel} leaked into the system turn"
        assert "OBS-NARRATIVE" in role.untrusted_reference
        assert "OBS-NARRATIVE" not in role.system


def test_generated_scenario_contract_defaults_to_the_untrusted_turn() -> None:
    # The solve path generates rules/interface/criteria with an LLM; without an
    # explicit trusted assertion they must NOT get system authority.
    parts = _build_classified()  # scenario_contract_trusted defaults False
    for role in (parts.competitor, parts.analyst, parts.coach, parts.architect):
        for sentinel in _CONTRACT.values():
            assert sentinel in role.untrusted_reference, f"{sentinel} missing from untrusted turn"
            assert sentinel not in role.system, f"{sentinel} leaked into the system turn"


def test_operator_asserted_contract_is_trusted_in_system() -> None:
    parts = _build_classified(scenario_contract_trusted=True)
    for role in (parts.competitor, parts.analyst, parts.coach, parts.architect):
        for sentinel in _CONTRACT.values():
            assert sentinel in role.system, f"{sentinel} missing from the system turn"
            assert sentinel not in role.untrusted_reference


def test_scout_guidance_is_a_trusted_system_instruction() -> None:
    # Deterministic code-authored scout instructions must stay authoritative
    # (system turn), not land in the guarded user turn the guardrail says to ignore.
    parts = _build_classified()
    assert "SCOUT-GUIDANCE" in parts.competitor.system
    assert "SCOUT-GUIDANCE" not in parts.competitor.untrusted_reference


def test_role_specific_derived_context_is_untrusted_and_scoped() -> None:
    parts = _build_classified()
    # Architect tool-usage report is architect-only derived context → its untrusted turn.
    assert "A-TOOLUSAGE" in parts.architect.untrusted_reference
    assert "A-TOOLUSAGE" not in parts.architect.system
    # Editable notebooks are untrusted and scoped to their role.
    assert "NB-COMPETITOR" in parts.competitor.untrusted_reference
    assert "NB-COMPETITOR" not in parts.competitor.system
    assert "NB-ANALYST" in parts.analyst.untrusted_reference
    assert "NB-COMPETITOR" not in parts.analyst.untrusted_reference  # not cross-contaminated
