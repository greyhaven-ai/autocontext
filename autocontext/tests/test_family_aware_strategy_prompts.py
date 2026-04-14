from __future__ import annotations

from autocontext.prompts.templates import build_prompt_bundle
from autocontext.scenarios.base import Observation

NUMERIC_INTERFACE = "Return JSON object with `aggression`, `defense`, and `path_bias` as floats in [0,1]."
ACTION_PLAN_INTERFACE = (
    "Return JSON with an ordered action plan:\n"
    "{\n"
    '  "actions": [\n'
    '    {"name": "action_name", "parameters": {...}, "reasoning": "why this step now"}\n'
    "  ]\n"
    "}\n\n"
    "Allowed action names: review_request, escalate_to_human_operator, continue_with_operator_guidance"
)


def _build_bundle(strategy_interface: str):
    return build_prompt_bundle(
        scenario_rules="Rules",
        strategy_interface=strategy_interface,
        evaluation_criteria="Criteria",
        previous_summary="Prev",
        observation=Observation(narrative="obs", state={}, constraints=[]),
        current_playbook="Playbook",
        available_tools="",
    )


class TestFamilyAwareCompetitorPrompt:
    def test_uses_action_plan_language_for_simulation_style_interfaces(self) -> None:
        bundle = _build_bundle(ACTION_PLAN_INTERFACE)

        assert "Return ONLY a JSON object" in bundle.competitor
        assert "reasoning` field" in bundle.competitor
        assert "parameter values" not in bundle.competitor

    def test_keeps_parameter_language_for_numeric_interfaces(self) -> None:
        bundle = _build_bundle(NUMERIC_INTERFACE)

        assert "parameter values" in bundle.competitor
