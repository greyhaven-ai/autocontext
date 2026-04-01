"""Tests for AC-250: negotiation and adversarial hidden-state scenario family.

Full vertical-slice tests covering:
- Data models (HiddenPreferences, NegotiationRound, OpponentModel, NegotiationResult)
- NegotiationInterface ABC
- Family registry integration
- Pipeline registry integration
- Classifier routing
- Designer/codegen
- Creator end-to-end (create → persist → load → register)
- AgentTaskCreator routing dispatch
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# ===========================================================================
# Data models
# ===========================================================================


class TestHiddenPreferences:
    def test_construction(self) -> None:
        from autocontext.scenarios.negotiation import HiddenPreferences

        prefs = HiddenPreferences(
            priorities={"price": 0.8, "delivery": 0.2},
            reservation_value=50.0,
            aspiration_value=90.0,
            batna_description="Walk away and find another vendor",
        )
        assert prefs.priorities["price"] == 0.8
        assert prefs.reservation_value == 50.0
        assert prefs.aspiration_value == 90.0
        assert prefs.batna_description == "Walk away and find another vendor"

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.negotiation import HiddenPreferences

        prefs = HiddenPreferences(
            priorities={"price": 0.6, "quality": 0.4},
            reservation_value=30.0,
            aspiration_value=80.0,
            batna_description="Use alternative supplier",
        )
        d = prefs.to_dict()
        restored = HiddenPreferences.from_dict(d)
        assert restored.priorities == prefs.priorities
        assert restored.reservation_value == prefs.reservation_value
        assert restored.aspiration_value == prefs.aspiration_value
        assert restored.batna_description == prefs.batna_description

    def test_defaults(self) -> None:
        from autocontext.scenarios.negotiation import HiddenPreferences

        prefs = HiddenPreferences(
            priorities={},
            reservation_value=0.0,
            aspiration_value=100.0,
            batna_description="",
        )
        assert prefs.metadata == {}


class TestNegotiationRound:
    def test_construction(self) -> None:
        from autocontext.scenarios.negotiation import NegotiationRound

        rnd = NegotiationRound(
            round_number=1,
            offer={"price": 70, "delivery_days": 5},
            counter_offer={"price": 80, "delivery_days": 3},
            accepted=False,
            agent_reasoning="Testing price sensitivity",
        )
        assert rnd.round_number == 1
        assert rnd.offer["price"] == 70
        assert rnd.counter_offer is not None
        assert rnd.accepted is False

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.negotiation import NegotiationRound

        rnd = NegotiationRound(
            round_number=2,
            offer={"price": 75},
            counter_offer=None,
            accepted=True,
            agent_reasoning="Final deal",
        )
        d = rnd.to_dict()
        restored = NegotiationRound.from_dict(d)
        assert restored.round_number == rnd.round_number
        assert restored.offer == rnd.offer
        assert restored.counter_offer is None
        assert restored.accepted is True
        assert restored.agent_reasoning == "Final deal"


class TestOpponentModel:
    def test_construction(self) -> None:
        from autocontext.scenarios.negotiation import OpponentModel

        model = OpponentModel(
            inferred_priorities={"price": 0.7, "quality": 0.3},
            inferred_reservation=40.0,
            strategy_hypothesis="Anchoring high then conceding gradually",
            confidence=0.6,
            adaptation_notes=["Noticed price sensitivity after round 2"],
        )
        assert model.inferred_priorities["price"] == 0.7
        assert model.confidence == 0.6
        assert len(model.adaptation_notes) == 1

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.negotiation import OpponentModel

        model = OpponentModel(
            inferred_priorities={"speed": 0.9},
            inferred_reservation=20.0,
            strategy_hypothesis="Aggressive deadline pressure",
            confidence=0.8,
            adaptation_notes=[],
        )
        d = model.to_dict()
        restored = OpponentModel.from_dict(d)
        assert restored.inferred_priorities == model.inferred_priorities
        assert restored.inferred_reservation == model.inferred_reservation
        assert restored.strategy_hypothesis == model.strategy_hypothesis
        assert restored.confidence == model.confidence


class TestNegotiationResult:
    def test_construction(self) -> None:
        from autocontext.scenarios.negotiation import NegotiationResult

        result = NegotiationResult(
            score=0.75,
            reasoning="Good deal quality, decent opponent modeling",
            dimension_scores={
                "deal_quality": 0.8,
                "opponent_modeling": 0.7,
                "efficiency": 0.6,
                "adaptation": 0.9,
            },
            deal_value=72.0,
            rounds_used=3,
            max_rounds=5,
            opponent_model_accuracy=0.7,
            value_claimed_ratio=0.65,
        )
        assert result.score == 0.75
        assert result.dimension_scores["deal_quality"] == 0.8
        assert result.deal_value == 72.0
        assert result.rounds_used == 3
        assert result.opponent_model_accuracy == 0.7

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.negotiation import NegotiationResult

        result = NegotiationResult(
            score=0.5,
            reasoning="Mediocre",
            dimension_scores={"deal_quality": 0.5},
            deal_value=50.0,
            rounds_used=5,
            max_rounds=5,
            opponent_model_accuracy=0.3,
            value_claimed_ratio=0.4,
        )
        d = result.to_dict()
        restored = NegotiationResult.from_dict(d)
        assert restored.score == result.score
        assert restored.deal_value == result.deal_value
        assert restored.rounds_used == result.rounds_used


# ===========================================================================
# NegotiationInterface ABC
# ===========================================================================


class TestNegotiationInterface:
    def test_cannot_instantiate(self) -> None:
        from autocontext.scenarios.negotiation import NegotiationInterface

        with pytest.raises(TypeError):
            NegotiationInterface()  # type: ignore[abstract]

    def test_concrete_subclass(self) -> None:
        from autocontext.scenarios.negotiation import (
            HiddenPreferences,
            NegotiationInterface,
            NegotiationResult,
            NegotiationRound,
            OpponentModel,
        )
        from autocontext.scenarios.simulation import (
            Action,
            ActionResult,
            ActionSpec,
            ActionTrace,
            EnvironmentSpec,
            SimulationResult,
        )

        class Stub(NegotiationInterface):
            name = "stub_negotiation"

            def describe_scenario(self) -> str:
                return "stub"

            def describe_environment(self) -> EnvironmentSpec:
                return EnvironmentSpec(
                    name="stub",
                    description="stub",
                    available_actions=[],
                    initial_state_description="stub",
                    success_criteria=[],
                    failure_modes=[],
                )

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"round": 0}

            def get_available_actions(
                self, state: dict[str, Any]
            ) -> list[ActionSpec]:
                return []

            def validate_action(
                self, state: dict[str, Any], action: Action
            ) -> tuple[bool, str]:
                return True, ""

            def execute_action(
                self, state: dict[str, Any], action: Action
            ) -> tuple[ActionResult, dict[str, Any]]:
                return ActionResult(
                    success=True, output="ok", state_changes={}
                ), state

            def is_terminal(self, state: dict[str, Any]) -> bool:
                return True

            def evaluate_trace(
                self, trace: ActionTrace, final_state: dict[str, Any]
            ) -> SimulationResult:
                return SimulationResult(
                    score=1.0,
                    reasoning="ok",
                    dimension_scores={},
                    workflow_complete=True,
                    actions_taken=0,
                    actions_successful=0,
                )

            def get_rubric(self) -> str:
                return "rubric"

            def max_steps(self) -> int:
                return 5

            def get_hidden_preferences(
                self, state: dict[str, Any]
            ) -> HiddenPreferences:
                return HiddenPreferences(
                    priorities={}, reservation_value=0.0,
                    aspiration_value=100.0, batna_description="none",
                )

            def get_rounds(
                self, state: dict[str, Any]
            ) -> list[NegotiationRound]:
                return []

            def get_opponent_model(
                self, state: dict[str, Any]
            ) -> OpponentModel | None:
                return None

            def update_opponent_model(
                self, state: dict[str, Any], model: OpponentModel
            ) -> dict[str, Any]:
                return state

            def evaluate_negotiation(
                self, state: dict[str, Any]
            ) -> NegotiationResult:
                return NegotiationResult(
                    score=0.5, reasoning="stub", dimension_scores={},
                    deal_value=50.0, rounds_used=0, max_rounds=5,
                    opponent_model_accuracy=0.0, value_claimed_ratio=0.0,
                )

        stub = Stub()
        assert stub.name == "stub_negotiation"
        prefs = stub.get_hidden_preferences({"round": 0})
        assert isinstance(prefs, HiddenPreferences)
        rounds = stub.get_rounds({"round": 0})
        assert isinstance(rounds, list)
        assert stub.get_opponent_model({"round": 0}) is None
        result = stub.evaluate_negotiation({"round": 0})
        assert isinstance(result, NegotiationResult)
        assert result.score == 0.5


# ===========================================================================
# Family registry integration
# ===========================================================================


class TestFamilyRegistration:
    def test_family_registered(self) -> None:
        from autocontext.scenarios.families import FAMILY_REGISTRY

        assert "negotiation" in FAMILY_REGISTRY

    def test_family_marker(self) -> None:
        from autocontext.scenarios.families import get_family_marker

        assert get_family_marker("negotiation") == "negotiation"

    def test_detect_family(self) -> None:
        """detect_family should resolve a NegotiationInterface instance."""
        from autocontext.scenarios.families import detect_family
        from autocontext.scenarios.negotiation import NegotiationInterface
        from autocontext.scenarios.simulation import (
            Action,
            ActionResult,
            ActionSpec,
            ActionTrace,
            EnvironmentSpec,
            SimulationResult,
        )

        class MinimalNeg(NegotiationInterface):
            name = "minimal_neg"

            def describe_scenario(self) -> str:
                return ""

            def describe_environment(self) -> EnvironmentSpec:
                return EnvironmentSpec(
                    name="", description="", available_actions=[],
                    initial_state_description="", success_criteria=[],
                    failure_modes=[],
                )

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {}

            def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
                return []

            def validate_action(self, state: dict[str, Any], action: Action) -> tuple[bool, str]:
                return True, ""

            def execute_action(
                self, state: dict[str, Any], action: Action
            ) -> tuple[ActionResult, dict[str, Any]]:
                return ActionResult(success=True, output="", state_changes={}), state

            def is_terminal(self, state: dict[str, Any]) -> bool:
                return True

            def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
                return SimulationResult(
                    score=0.0, reasoning="", dimension_scores={},
                    workflow_complete=False, actions_taken=0, actions_successful=0,
                )

            def get_rubric(self) -> str:
                return ""

            def max_steps(self) -> int:
                return 1

            def get_hidden_preferences(self, state: dict[str, Any]) -> Any:
                from autocontext.scenarios.negotiation import HiddenPreferences
                return HiddenPreferences(
                    priorities={}, reservation_value=0.0,
                    aspiration_value=0.0, batna_description="",
                )

            def get_rounds(self, state: dict[str, Any]) -> list:
                return []

            def get_opponent_model(self, state: dict[str, Any]) -> Any:
                return None

            def update_opponent_model(self, state: dict[str, Any], model: Any) -> dict[str, Any]:
                return state

            def evaluate_negotiation(self, state: dict[str, Any]) -> Any:
                from autocontext.scenarios.negotiation import NegotiationResult
                return NegotiationResult(
                    score=0.0, reasoning="", dimension_scores={},
                    deal_value=0.0, rounds_used=0, max_rounds=1,
                    opponent_model_accuracy=0.0, value_claimed_ratio=0.0,
                )

        family = detect_family(MinimalNeg())
        assert family is not None
        assert family.name == "negotiation"


# ===========================================================================
# Pipeline registry integration
# ===========================================================================


class TestPipelineRegistration:
    def test_pipeline_registered(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import PIPELINE_REGISTRY

        assert "negotiation" in PIPELINE_REGISTRY

    def test_spec_validation_valid(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec = {
            "description": "Negotiate a contract",
            "environment_description": "Two-party contract negotiation",
            "initial_state_description": "Opening positions set",
            "hidden_preferences": {
                "priorities": {"price": 0.7},
                "reservation_value": 40.0,
                "aspiration_value": 90.0,
                "batna_description": "Walk away",
            },
            "max_rounds": 5,
            "success_criteria": ["reach agreement above reservation"],
            "failure_modes": ["deadlock"],
            "actions": [
                {"name": "make_offer", "description": "d", "parameters": {},
                 "preconditions": [], "effects": []}
            ],
        }
        errors = validate_for_family("negotiation", spec)
        assert errors == []

    def test_spec_validation_missing_fields(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        errors = validate_for_family("negotiation", {"description": "x"})
        assert len(errors) > 0

    def test_source_validation(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        bad_source = "class Foo:\n    pass\n"
        errors = validate_source_for_family("negotiation", bad_source)
        assert len(errors) > 0


# ===========================================================================
# Cross-family mismatch
# ===========================================================================


class TestCrossFamilyMismatch:
    def test_negotiation_source_fails_tool_fragility_pipeline(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = "class MyNeg(NegotiationInterface):\n    pass\n"
        errors = validate_source_for_family("tool_fragility", source)
        assert len(errors) > 0

    def test_tool_fragility_source_fails_negotiation_pipeline(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = "class MyFrag(ToolFragilityInterface):\n    pass\n"
        errors = validate_source_for_family("negotiation", source)
        assert len(errors) > 0


# ===========================================================================
# Classifier routing (hot path: classify → route)
# ===========================================================================


class TestClassifierRouting:
    def test_route_negotiation(self) -> None:
        from autocontext.scenarios.custom.family_classifier import (
            classify_scenario_family,
            route_to_family,
        )

        classification = classify_scenario_family(
            "Negotiation scenario with hidden preferences where agents "
            "model the opponent and adapt strategy across repeated rounds"
        )
        family = route_to_family(classification)
        assert family.name == "negotiation"

    def test_negotiate_keyword_matches(self) -> None:
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family

        classification = classify_scenario_family(
            "Negotiate a contract deal with BATNA constraints "
            "and opponent modeling across multiple rounds"
        )
        assert classification.family_name == "negotiation"


# ===========================================================================
# Designer/spec parsing (hot path: design)
# ===========================================================================


class TestNegotiationDesigner:
    def test_parse_spec(self) -> None:
        from autocontext.scenarios.custom.negotiation_designer import (
            NEGOTIATION_SPEC_END,
            NEGOTIATION_SPEC_START,
            parse_negotiation_spec,
        )

        raw = f"""{NEGOTIATION_SPEC_START}
{{
    "description": "Contract negotiation",
    "environment_description": "Two parties",
    "initial_state_description": "Opening bids",
    "hidden_preferences": {{
        "priorities": {{"price": 0.7, "quality": 0.3}},
        "reservation_value": 40.0,
        "aspiration_value": 85.0,
        "batna_description": "Use alternative vendor"
    }},
    "max_rounds": 5,
    "success_criteria": ["reach deal above reservation"],
    "failure_modes": ["deadlock", "accept below BATNA"],
    "actions": [
        {{
            "name": "make_offer", "description": "propose terms",
            "parameters": {{"terms": "dict"}},
            "preconditions": [], "effects": ["offer_made"]
        }},
        {{
            "name": "accept", "description": "accept current terms",
            "parameters": {{}},
            "preconditions": ["make_offer"], "effects": ["deal_closed"]
        }}
    ]
}}
{NEGOTIATION_SPEC_END}"""
        spec = parse_negotiation_spec(raw)
        assert spec.description == "Contract negotiation"
        assert spec.hidden_preferences["priorities"]["price"] == 0.7
        assert spec.max_rounds == 5
        assert len(spec.actions) == 2

    def test_design_fn_calls_llm(self) -> None:
        import json

        from autocontext.scenarios.custom.negotiation_designer import (
            NEGOTIATION_SPEC_END,
            NEGOTIATION_SPEC_START,
            design_negotiation,
        )

        fake_spec = {
            "description": "test",
            "environment_description": "env",
            "initial_state_description": "init",
            "hidden_preferences": {
                "priorities": {"price": 0.5},
                "reservation_value": 30.0,
                "aspiration_value": 80.0,
                "batna_description": "walk away",
            },
            "max_rounds": 3,
            "success_criteria": ["ok"],
            "failure_modes": [],
            "actions": [
                {
                    "name": "offer", "description": "o",
                    "parameters": {}, "preconditions": [], "effects": [],
                }
            ],
        }

        def fake_llm(system: str, user: str) -> str:
            return (
                f"{NEGOTIATION_SPEC_START}\n"
                f"{json.dumps(fake_spec)}\n"
                f"{NEGOTIATION_SPEC_END}"
            )

        spec = design_negotiation("test negotiation", fake_llm)
        assert spec.description == "test"
        assert spec.max_rounds == 3


# ===========================================================================
# Codegen (hot path: generate source)
# ===========================================================================


class TestNegotiationCodegen:
    def test_generate_class(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import (
            validate_source_for_family,
        )
        from autocontext.scenarios.custom.negotiation_codegen import (
            generate_negotiation_class,
        )
        from autocontext.scenarios.custom.negotiation_spec import (
            NegotiationSpec,
        )
        from autocontext.scenarios.custom.simulation_spec import (
            SimulationActionSpecModel,
        )

        spec = NegotiationSpec(
            description="test negotiation",
            environment_description="env",
            initial_state_description="init",
            hidden_preferences={
                "priorities": {"price": 0.6},
                "reservation_value": 30.0,
                "aspiration_value": 80.0,
                "batna_description": "walk",
            },
            max_rounds=3,
            success_criteria=["deal above reservation"],
            failure_modes=["deadlock"],
            actions=[
                SimulationActionSpecModel(
                    name="make_offer",
                    description="propose terms",
                    parameters={"terms": "dict"},
                    preconditions=[],
                    effects=["offer_made"],
                ),
                SimulationActionSpecModel(
                    name="accept",
                    description="accept terms",
                    parameters={},
                    preconditions=["make_offer"],
                    effects=["deal_closed"],
                ),
            ],
        )
        source = generate_negotiation_class(spec, name="test_neg")
        errors = validate_source_for_family("negotiation", source)
        assert errors == [], f"validation errors: {errors}"

    def test_generated_source_compiles(self) -> None:
        from autocontext.scenarios.custom.negotiation_codegen import (
            generate_negotiation_class,
        )
        from autocontext.scenarios.custom.negotiation_spec import (
            NegotiationSpec,
        )
        from autocontext.scenarios.custom.simulation_spec import (
            SimulationActionSpecModel,
        )

        spec = NegotiationSpec(
            description="compile test",
            environment_description="env",
            initial_state_description="init",
            hidden_preferences={
                "priorities": {"speed": 0.5},
                "reservation_value": 20.0,
                "aspiration_value": 70.0,
                "batna_description": "none",
            },
            max_rounds=2,
            success_criteria=["ok"],
            failure_modes=[],
            actions=[
                SimulationActionSpecModel(
                    name="bid", description="bid", parameters={},
                    preconditions=[], effects=["bid_done"],
                ),
            ],
        )
        source = generate_negotiation_class(spec, name="compile_test")
        compile(source, "<test>", "exec")


# ===========================================================================
# Creator end-to-end (hot path: create → persist → load → register)
# ===========================================================================


class TestNegotiationCreator:
    def test_create_and_persist(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.custom.creator_registry import create_for_family
        from autocontext.scenarios.custom.negotiation_designer import (
            NEGOTIATION_SPEC_END,
            NEGOTIATION_SPEC_START,
        )
        from autocontext.scenarios.negotiation import NegotiationInterface

        fake_spec = {
            "description": "test creation",
            "environment_description": "env",
            "initial_state_description": "init",
            "hidden_preferences": {
                "priorities": {"p": 0.5},
                "reservation_value": 25.0,
                "aspiration_value": 75.0,
                "batna_description": "leave",
            },
            "max_rounds": 3,
            "success_criteria": ["done"],
            "failure_modes": [],
            "actions": [
                {
                    "name": "offer", "description": "o",
                    "parameters": {}, "preconditions": [], "effects": [],
                }
            ],
        }

        def fake_llm(system: str, user: str) -> str:
            return (
                f"{NEGOTIATION_SPEC_START}\n"
                f"{json.dumps(fake_spec)}\n"
                f"{NEGOTIATION_SPEC_END}"
            )

        creator = create_for_family("negotiation", fake_llm, tmp_path)
        scenario = creator.create("test negotiation", name="test_neg_creator")
        assert isinstance(scenario, NegotiationInterface)

        scenario_dir = tmp_path / "_custom_scenarios" / "test_neg_creator"
        assert (scenario_dir / "scenario.py").exists()
        assert (scenario_dir / "spec.json").exists()
        assert (scenario_dir / "scenario_type.txt").exists()
        assert (
            (scenario_dir / "scenario_type.txt").read_text().strip()
            == "negotiation"
        )


# ===========================================================================
# Router dispatch from AgentTaskCreator (hot path: routing)
# ===========================================================================


class TestAgentTaskCreatorRouting:
    def test_routes_to_negotiation(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.custom.agent_task_creator import (
            AgentTaskCreator,
        )
        from autocontext.scenarios.custom.negotiation_designer import (
            NEGOTIATION_SPEC_END,
            NEGOTIATION_SPEC_START,
        )
        from autocontext.scenarios.negotiation import NegotiationInterface

        fake_spec = {
            "description": "routing test",
            "environment_description": "env",
            "initial_state_description": "init",
            "hidden_preferences": {
                "priorities": {"p": 0.5},
                "reservation_value": 20.0,
                "aspiration_value": 70.0,
                "batna_description": "walk",
            },
            "max_rounds": 3,
            "success_criteria": ["done"],
            "failure_modes": [],
            "actions": [
                {
                    "name": "offer", "description": "o",
                    "parameters": {}, "preconditions": [], "effects": [],
                }
            ],
        }

        def fake_llm(system: str, user: str) -> str:
            return (
                f"{NEGOTIATION_SPEC_START}\n"
                f"{json.dumps(fake_spec)}\n"
                f"{NEGOTIATION_SPEC_END}"
            )

        creator = AgentTaskCreator(fake_llm, tmp_path)
        scenario = creator.create(
            "Negotiation scenario with hidden preferences "
            "where agents model the opponent across repeated rounds"
        )
        assert isinstance(scenario, NegotiationInterface)
