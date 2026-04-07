from __future__ import annotations

from autocontext.scenarios.custom.simulation_spec import (
    SimulationActionSpecModel,
    normalize_simulation_spec_dict,
)


class TestSimulationActionSpecModelNormalization:
    def test_from_dict_maps_postconditions_to_effects(self) -> None:
        action = SimulationActionSpecModel.from_dict({
            "name": "triage",
            "description": "Triage the issue",
            "parameters": {},
            "preconditions": [],
            "postconditions": ["triaged"],
        })

        assert action.effects == ["triaged"]

    def test_from_dict_prefers_explicit_effects_when_present(self) -> None:
        action = SimulationActionSpecModel.from_dict({
            "name": "escalate",
            "description": "Escalate the incident",
            "parameters": {},
            "effects": ["paged"],
            "postconditions": ["triaged"],
        })

        assert action.effects == ["paged"]

    def test_normalize_simulation_spec_dict_coerces_llm_friendly_shapes(self) -> None:
        normalized = normalize_simulation_spec_dict({
            "description": "Support escalation sim",
            "environment_description": "Prod",
            "initial_state_description": "Start",
            "success_criteria": [{"condition": "resolved", "description": "Incident resolved"}],
            "failure_modes": [{"condition": "timeout", "description": "Timed out"}],
            "actions": [
                {
                    "name": "gather_info",
                    "description": "Gather info",
                    "parameters": {},
                    "postconditions": [{"description": "Evidence collected"}],
                    "steps": [{"action": "observe", "condition": "always"}],
                },
            ],
        })

        assert normalized["success_criteria"] == ["Incident resolved"]
        assert normalized["failure_modes"] == ["Timed out"]
        assert normalized["actions"][0]["effects"] == ["Evidence collected"]
        assert "postconditions" not in normalized["actions"][0]
        assert "steps" not in normalized["actions"][0]

    def test_from_dict_prefers_action_ids_for_structured_preconditions(self) -> None:
        action = SimulationActionSpecModel.from_dict({
            "name": "step_b",
            "description": "Second",
            "parameters": {},
            "preconditions": [{"action": "step_a", "description": "after step a"}],
            "effects": ["b_done"],
        })

        assert action.preconditions == ["step_a"]
