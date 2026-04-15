"""Tests for AC-246: Natural-language scenario-family inference and routing.

Validates the family classifier that infers the intended scenario family
from a natural-language description before spec generation, returning
ranked choices with confidence and rationale, and routing into the
correct family-specific generator.
"""

from __future__ import annotations

import pytest

from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.family_classifier import (
    _DEFAULT_FAMILY_NAME,
    FamilyCandidate,
    FamilyClassification,
    LowConfidenceError,
    classify_scenario_family,
    route_to_family,
)
from autocontext.scenarios.families import FAMILY_REGISTRY, ScenarioFamily, list_families, register_family

# ---------------------------------------------------------------------------
# FamilyCandidate / FamilyClassification data models
# ---------------------------------------------------------------------------


class TestFamilyCandidate:
    def test_construction(self) -> None:
        candidate = FamilyCandidate(
            family_name="simulation",
            confidence=0.85,
            rationale="Description mentions API orchestration and rollback",
        )
        assert candidate.family_name == "simulation"
        assert candidate.confidence == 0.85
        assert "rollback" in candidate.rationale


class TestFamilyClassification:
    def test_construction(self) -> None:
        classification = FamilyClassification(
            family_name="agent_task",
            confidence=0.9,
            rationale="Content generation task",
            alternatives=[
                FamilyCandidate(family_name="game", confidence=0.1, rationale="low match"),
            ],
        )
        assert classification.family_name == "agent_task"
        assert classification.confidence == 0.9
        assert len(classification.alternatives) == 1

    def test_to_dict_roundtrip(self) -> None:
        classification = FamilyClassification(
            family_name="simulation",
            confidence=0.75,
            rationale="Workflow orchestration detected",
            alternatives=[
                FamilyCandidate(family_name="agent_task", confidence=0.2, rationale="some text keywords"),
            ],
        )
        data = classification.to_dict()
        assert data["family_name"] == "simulation"
        assert data["confidence"] == 0.75
        assert len(data["alternatives"]) == 1
        assert data["alternatives"][0]["family_name"] == "agent_task"

        restored = FamilyClassification.from_dict(data)
        assert restored.family_name == classification.family_name
        assert restored.confidence == classification.confidence
        assert restored.rationale == classification.rationale
        assert len(restored.alternatives) == len(classification.alternatives)

    def test_empty_alternatives(self) -> None:
        classification = FamilyClassification(
            family_name="game",
            confidence=1.0,
            rationale="Clear game scenario",
            alternatives=[],
        )
        data = classification.to_dict()
        assert data["alternatives"] == []


# ---------------------------------------------------------------------------
# classify_scenario_family — simulation signals
# ---------------------------------------------------------------------------


class TestClassifySimulation:
    def test_api_orchestration(self) -> None:
        result = classify_scenario_family(
            "Build a scenario where an agent orchestrates API calls across microservices "
            "and must handle failures with rollback"
        )
        assert result.family_name == "simulation"
        assert result.confidence >= 0.5

    def test_deployment_workflow(self) -> None:
        result = classify_scenario_family(
            "Create a deployment pipeline simulation where the agent must deploy services "
            "in the correct order and recover from failures"
        )
        assert result.family_name == "simulation"

    def test_debugging_with_state(self) -> None:
        result = classify_scenario_family(
            "Simulate a debugging scenario where the agent investigates server logs, "
            "queries monitoring dashboards, and traces the root cause through API calls"
        )
        assert result.family_name == "simulation"

    def test_incident_response(self) -> None:
        result = classify_scenario_family(
            "Create an incident response simulation where the agent must triage alerts, "
            "check service health endpoints, and execute remediation steps"
        )
        assert result.family_name == "simulation"

    def test_geopolitical_crisis_routes_to_simulation(self) -> None:
        result = classify_scenario_family(
            "Create a geopolitical crisis simulation where a national security advisor manages "
            "an escalating international confrontation using diplomatic, economic, military, "
            "intelligence, public communication, alliance, UN, humanitarian, and cyber actions "
            "under hidden adversary objectives and escalation thresholds."
        )
        assert result.family_name == "simulation"
        assert result.confidence >= 0.3


class TestClassifyArtifactEditing:
    def test_config_editing(self) -> None:
        result = classify_scenario_family(
            "Create a task where the agent must edit a YAML config file to add a missing database section"
        )
        assert result.family_name == "artifact_editing"

    def test_schema_migration(self) -> None:
        result = classify_scenario_family(
            "Build an artifact editing scenario that updates a JSON schema and repairs a broken SQL migration"
        )
        assert result.family_name == "artifact_editing"


class TestClassifyInvestigation:
    def test_root_cause_investigation(self) -> None:
        result = classify_scenario_family(
            "Create an investigation scenario where the agent must gather evidence, avoid red herrings, "
            "and identify the root cause of a production outage"
        )
        assert result.family_name == "investigation"


class TestClassifyWorkflow:
    def test_transactional_workflow(self) -> None:
        result = classify_scenario_family(
            "Create a transactional workflow where the agent must execute payment, inventory, and "
            "notification steps with compensation for reversible side effects"
        )
        assert result.family_name == "workflow"


# ---------------------------------------------------------------------------
# classify_scenario_family — agent_task signals
# ---------------------------------------------------------------------------


class TestClassifyAgentTask:
    def test_essay_writing(self) -> None:
        result = classify_scenario_family(
            "Evaluate an agent's ability to write a persuasive essay about climate change"
        )
        assert result.family_name == "agent_task"

    def test_code_generation(self) -> None:
        result = classify_scenario_family(
            "Generate a Python function that sorts a list of dictionaries by multiple keys"
        )
        assert result.family_name == "agent_task"

    def test_content_summarization(self) -> None:
        result = classify_scenario_family(
            "Summarize a long research paper into a concise abstract"
        )
        assert result.family_name == "agent_task"

    def test_data_analysis_report(self) -> None:
        result = classify_scenario_family(
            "Analyze a dataset of customer reviews and produce a sentiment report"
        )
        assert result.family_name == "agent_task"


# ---------------------------------------------------------------------------
# classify_scenario_family — game signals
# ---------------------------------------------------------------------------


class TestClassifyGame:
    def test_board_game(self) -> None:
        result = classify_scenario_family(
            "Create a competitive board game where two players compete for territory control"
        )
        assert result.family_name == "game"

    def test_strategy_tournament(self) -> None:
        result = classify_scenario_family(
            "Design a tournament where strategies compete head-to-head in a resource "
            "management game with scoring based on efficiency"
        )
        assert result.family_name == "game"

    def test_capture_the_flag(self) -> None:
        result = classify_scenario_family(
            "Build a capture the flag grid game where opponents navigate a maze"
        )
        assert result.family_name == "game"


# ---------------------------------------------------------------------------
# classify_scenario_family — alternatives and ranking
# ---------------------------------------------------------------------------


class TestClassificationAlternatives:
    def test_alternatives_are_ranked_by_confidence(self) -> None:
        result = classify_scenario_family(
            "Build a deployment pipeline simulation where the agent must deploy services"
        )
        if result.alternatives:
            confidences = [a.confidence for a in result.alternatives]
            assert confidences == sorted(confidences, reverse=True)

    def test_alternatives_cover_other_families(self) -> None:
        result = classify_scenario_family(
            "Write an essay about the history of computing"
        )
        alt_names = {a.family_name for a in result.alternatives}
        # Alternatives should include families other than the top choice
        assert result.family_name not in alt_names

    def test_all_families_represented(self) -> None:
        """Top choice + alternatives should cover all registered families."""
        result = classify_scenario_family(
            "Create a scenario for testing API orchestration with rollback"
        )
        all_names = {result.family_name} | {a.family_name for a in result.alternatives}
        assert all_names == {family.name for family in list_families()}

    def test_registered_families_drive_low_signal_alternatives(self) -> None:
        temp_family = ScenarioFamily(
            name="_test_family",
            description="Temporary test family",
            interface_class=ScenarioInterface,
            evaluation_mode="custom",
            output_modes=["free_text"],
            scenario_type_marker="_test_family",
        )
        register_family(temp_family)
        try:
            result = classify_scenario_family("do something unusual")
            all_names = {result.family_name} | {a.family_name for a in result.alternatives}
            assert "_test_family" in all_names
            assert result.family_name == _DEFAULT_FAMILY_NAME
        finally:
            FAMILY_REGISTRY.pop("_test_family", None)


# ---------------------------------------------------------------------------
# classify_scenario_family — edge cases
# ---------------------------------------------------------------------------


class TestClassifyEdgeCases:
    def test_empty_description_raises(self) -> None:
        with pytest.raises(ValueError, match="description"):
            classify_scenario_family("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="description"):
            classify_scenario_family("   ")

    def test_very_short_description(self) -> None:
        """Short descriptions should still produce a classification."""
        result = classify_scenario_family("write code")
        assert result.family_name in {"agent_task", "game", "simulation"}
        assert result.confidence > 0.0

    def test_ambiguous_description_has_lower_confidence(self) -> None:
        """A vague description should produce lower confidence than a clear one."""
        clear = classify_scenario_family(
            "Build a competitive two-player board game tournament"
        )
        vague = classify_scenario_family(
            "Do something interesting with data"
        )
        assert clear.confidence > vague.confidence


# ---------------------------------------------------------------------------
# route_to_family — maps classification to ScenarioFamily
# ---------------------------------------------------------------------------


class TestRouteToFamily:
    def test_route_high_confidence(self) -> None:
        classification = FamilyClassification(
            family_name="simulation",
            confidence=0.85,
            rationale="API orchestration",
            alternatives=[],
        )
        family = route_to_family(classification)
        assert isinstance(family, ScenarioFamily)
        assert family.name == "simulation"

    def test_route_low_confidence_raises(self) -> None:
        classification = FamilyClassification(
            family_name="game",
            confidence=0.15,
            rationale="Weak signal",
            alternatives=[],
        )
        with pytest.raises(LowConfidenceError) as exc_info:
            route_to_family(classification, min_confidence=0.3)
        assert exc_info.value.classification is classification

    def test_route_custom_threshold(self) -> None:
        classification = FamilyClassification(
            family_name="agent_task",
            confidence=0.4,
            rationale="Moderate",
            alternatives=[],
        )
        # Should pass at threshold=0.3
        family = route_to_family(classification, min_confidence=0.3)
        assert family.name == "agent_task"

        # Should fail at threshold=0.5
        with pytest.raises(LowConfidenceError):
            route_to_family(classification, min_confidence=0.5)

    def test_route_unknown_family_raises(self) -> None:
        classification = FamilyClassification(
            family_name="nonexistent",
            confidence=0.9,
            rationale="Unknown",
            alternatives=[],
        )
        with pytest.raises(KeyError, match="Unknown scenario family"):
            route_to_family(classification)


# ---------------------------------------------------------------------------
# LowConfidenceError
# ---------------------------------------------------------------------------


class TestLowConfidenceError:
    def test_carries_classification(self) -> None:
        classification = FamilyClassification(
            family_name="game",
            confidence=0.1,
            rationale="Weak",
            alternatives=[
                FamilyCandidate(family_name="agent_task", confidence=0.08, rationale="Also weak"),
            ],
        )
        error = LowConfidenceError(classification, min_confidence=0.3)
        assert error.classification is classification
        assert error.min_confidence == 0.3
        assert "0.1" in str(error) or "0.10" in str(error)
        assert "0.3" in str(error) or "0.30" in str(error)


# ---------------------------------------------------------------------------
# Integration: classify + route end-to-end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_simulation_request_routes_correctly(self) -> None:
        classification = classify_scenario_family(
            "Create a workflow orchestration scenario where the agent must call APIs "
            "in the correct dependency order and handle failures with rollback"
        )
        family = route_to_family(classification)
        assert family.name == "simulation"
        assert family.evaluation_mode == "trace_evaluation"

    def test_agent_task_request_routes_correctly(self) -> None:
        classification = classify_scenario_family(
            "Write a persuasive blog post about sustainable energy"
        )
        family = route_to_family(classification)
        assert family.name == "agent_task"
        assert family.evaluation_mode == "llm_judge"

    def test_game_request_routes_correctly(self) -> None:
        classification = classify_scenario_family(
            "Design a competitive two-player strategy game with territory control"
        )
        family = route_to_family(classification)
        assert family.name == "game"
        assert family.evaluation_mode == "tournament"

    def test_previously_collapsed_request_no_longer_defaults_to_task(self) -> None:
        """Debugging/orchestration requests should NOT default to agent_task.

        This is the core issue: Level 10 API Orchestration was collapsing
        into narrative-only prose because the system defaulted to agent_task.
        """
        classification = classify_scenario_family(
            "Create an API orchestration scenario where an agent must call "
            "multiple microservice endpoints in order, handle dependency failures, "
            "and execute rollback procedures when deployments fail"
        )
        assert classification.family_name != "agent_task"
        assert classification.family_name == "simulation"
