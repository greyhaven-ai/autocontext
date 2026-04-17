from __future__ import annotations

from autocontext.simulation.helpers import infer_family


class TestInferFamily:
    def test_routes_geopolitical_crisis_to_simulation(self) -> None:
        family = infer_family(
            "Simulate a geopolitical crisis where a national security advisor manages "
            "an escalating international confrontation using diplomatic, economic, military, "
            "intelligence, public communication, alliance, UN, humanitarian, and cyber actions "
            "under hidden adversary objectives and escalation thresholds."
        )
        assert family == "simulation"

    def test_routes_ac276_geopolitical_stress_prompt_to_simulation(self) -> None:
        family = infer_family(
            "Harness Stress Test: geopolitical crisis wargame — multi-lever statecraft under hidden "
            "information and escalation dynamics. Build and run a geopolitical crisis simulation where "
            "the agent manages an escalating international crisis using NegotiationInterface + WorldState. "
            "Scenario seeds include Baltic hybrid warfare with ambiguous military movements and a "
            "cyber-kinetic infrastructure attack with attribution ambiguity. Early generations "
            "over-escalate or under-respond; later generations calibrate."
        )
        assert family == "simulation"

    def test_routes_compact_geopolitical_wargame_with_escalation_terms_to_simulation(self) -> None:
        family = infer_family(
            "Build a geopolitical crisis wargame with ambiguous military movements "
            "and over-escalation dynamics"
        )
        assert family == "simulation"

    def test_routes_statecraft_when_to_escalate_prompt_to_simulation(self) -> None:
        family = infer_family(
            "Simulate when to escalate diplomatic pressure during an international crisis "
            "with national security tradeoffs"
        )
        assert family == "simulation"

    def test_keeps_explicit_operator_loop_prompts_on_operator_loop(self) -> None:
        family = infer_family(
            "Simulate when an agent should escalate to a human operator, request clarification, "
            "and wait for approval before acting on ambiguous support tickets."
        )
        assert family == "operator_loop"

    def test_routes_clarification_only_prompts_to_operator_loop(self) -> None:
        assert infer_family("Handle requests with incomplete inputs before acting") == "operator_loop"
        assert infer_family("Handle ambiguous support tickets safely before acting") == "operator_loop"
