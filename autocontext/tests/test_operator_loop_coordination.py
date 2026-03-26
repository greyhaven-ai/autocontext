"""Tests for AC-251 + AC-253: operator-in-the-loop and multi-agent coordination families.

Full vertical-slice tests for both families:
- Data models
- Interface ABCs
- Family registry
- Pipeline registry
- Classifier routing
- Designer/codegen
- Creator (create → persist → load → register)
- AgentTaskCreator routing
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

# ===========================================================================
# AC-251: Operator-in-the-loop data models
# ===========================================================================


class TestClarificationRequest:
    def test_construction(self) -> None:
        from autocontext.scenarios.operator_loop import ClarificationRequest

        req = ClarificationRequest(
            question="What format should the output be in?",
            context="Processing customer data",
            urgency="medium",
        )
        assert req.question == "What format should the output be in?"
        assert req.urgency == "medium"

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.operator_loop import ClarificationRequest

        req = ClarificationRequest(
            question="q", context="c", urgency="low",
        )
        d = req.to_dict()
        restored = ClarificationRequest.from_dict(d)
        assert restored.question == req.question
        assert restored.context == req.context
        assert restored.urgency == req.urgency


class TestEscalationEvent:
    def test_construction(self) -> None:
        from autocontext.scenarios.operator_loop import EscalationEvent

        event = EscalationEvent(
            step=3,
            reason="Ambiguous requirements",
            severity="high",
            context="Customer request unclear",
            was_necessary=True,
        )
        assert event.step == 3
        assert event.severity == "high"
        assert event.was_necessary is True

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.operator_loop import EscalationEvent

        event = EscalationEvent(
            step=1, reason="r", severity="low",
            context="c", was_necessary=False,
        )
        d = event.to_dict()
        restored = EscalationEvent.from_dict(d)
        assert restored.step == event.step
        assert restored.was_necessary == event.was_necessary


class TestOperatorLoopResult:
    def test_construction(self) -> None:
        from autocontext.scenarios.operator_loop import OperatorLoopResult

        result = OperatorLoopResult(
            score=0.7,
            reasoning="Good judgment on when to escalate",
            dimension_scores={
                "action_quality": 0.8,
                "escalation_judgment": 0.7,
            },
            total_actions=10,
            escalations=2,
            necessary_escalations=1,
            unnecessary_escalations=1,
            missed_escalations=0,
            clarifications_requested=3,
        )
        assert result.score == 0.7
        assert result.escalations == 2
        assert result.unnecessary_escalations == 1

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.operator_loop import OperatorLoopResult

        result = OperatorLoopResult(
            score=0.5, reasoning="ok",
            dimension_scores={"action_quality": 0.5},
            total_actions=5, escalations=1,
            necessary_escalations=1, unnecessary_escalations=0,
            missed_escalations=0, clarifications_requested=1,
        )
        d = result.to_dict()
        restored = OperatorLoopResult.from_dict(d)
        assert restored.score == result.score
        assert restored.escalations == result.escalations


# ===========================================================================
# AC-251: OperatorLoopInterface ABC
# ===========================================================================


class TestOperatorLoopInterface:
    def test_cannot_instantiate(self) -> None:
        from autocontext.scenarios.operator_loop import OperatorLoopInterface

        with pytest.raises(TypeError):
            OperatorLoopInterface()  # type: ignore[abstract]

    def test_concrete_subclass(self) -> None:
        from autocontext.scenarios.operator_loop import (
            ClarificationRequest,
            EscalationEvent,
            OperatorLoopInterface,
            OperatorLoopResult,
        )
        from autocontext.scenarios.simulation import (
            Action,
            ActionResult,
            ActionSpec,
            ActionTrace,
            EnvironmentSpec,
            SimulationResult,
        )

        class Stub(OperatorLoopInterface):
            name = "stub_op_loop"

            def describe_scenario(self) -> str:
                return "stub"

            def describe_environment(self) -> EnvironmentSpec:
                return EnvironmentSpec(
                    name="stub", description="stub",
                    available_actions=[], initial_state_description="stub",
                    success_criteria=[], failure_modes=[],
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

            def evaluate_trace(
                self, trace: ActionTrace, final_state: dict[str, Any]
            ) -> SimulationResult:
                return SimulationResult(
                    score=0.0, reasoning="", dimension_scores={},
                    workflow_complete=True, actions_taken=0, actions_successful=0,
                )

            def get_rubric(self) -> str:
                return ""

            def max_steps(self) -> int:
                return 5

            def get_escalation_log(self, state: dict[str, Any]) -> list[EscalationEvent]:
                return []

            def get_clarification_log(self, state: dict[str, Any]) -> list[ClarificationRequest]:
                return []

            def escalate(
                self, state: dict[str, Any], event: EscalationEvent
            ) -> dict[str, Any]:
                return state

            def request_clarification(
                self, state: dict[str, Any], request: ClarificationRequest
            ) -> dict[str, Any]:
                return state

            def evaluate_judgment(self, state: dict[str, Any]) -> OperatorLoopResult:
                return OperatorLoopResult(
                    score=0.5, reasoning="", dimension_scores={},
                    total_actions=0, escalations=0,
                    necessary_escalations=0, unnecessary_escalations=0,
                    missed_escalations=0, clarifications_requested=0,
                )

        stub = Stub()
        assert stub.name == "stub_op_loop"
        assert stub.get_escalation_log({}) == []
        result = stub.evaluate_judgment({})
        assert isinstance(result, OperatorLoopResult)


# ===========================================================================
# AC-253: Multi-agent coordination data models
# ===========================================================================


class TestWorkerContext:
    def test_construction(self) -> None:
        from autocontext.scenarios.coordination import WorkerContext

        ctx = WorkerContext(
            worker_id="w1",
            role="researcher",
            context_partition={"visible_docs": ["doc1", "doc2"]},
            visible_data=["section_a"],
        )
        assert ctx.worker_id == "w1"
        assert ctx.role == "researcher"
        assert len(ctx.visible_data) == 1

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.coordination import WorkerContext

        ctx = WorkerContext(
            worker_id="w2", role="writer",
            context_partition={"topic": "x"},
            visible_data=["a", "b"],
        )
        d = ctx.to_dict()
        restored = WorkerContext.from_dict(d)
        assert restored.worker_id == ctx.worker_id
        assert restored.visible_data == ctx.visible_data


class TestHandoffRecord:
    def test_construction(self) -> None:
        from autocontext.scenarios.coordination import HandoffRecord

        handoff = HandoffRecord(
            from_worker="w1",
            to_worker="w2",
            content="Research findings on topic X",
            quality=0.8,
            step=2,
        )
        assert handoff.from_worker == "w1"
        assert handoff.quality == 0.8

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.coordination import HandoffRecord

        handoff = HandoffRecord(
            from_worker="a", to_worker="b",
            content="data", quality=0.5, step=1,
        )
        d = handoff.to_dict()
        restored = HandoffRecord.from_dict(d)
        assert restored.from_worker == handoff.from_worker
        assert restored.quality == handoff.quality


class TestCoordinationResult:
    def test_construction(self) -> None:
        from autocontext.scenarios.coordination import CoordinationResult

        result = CoordinationResult(
            score=0.75,
            reasoning="Good coordination",
            dimension_scores={
                "duplication_avoidance": 0.8,
                "handoff_quality": 0.7,
                "merge_quality": 0.8,
                "outcome_quality": 0.7,
            },
            workers_used=3,
            handoffs_completed=4,
            duplication_rate=0.1,
            merge_conflicts=1,
        )
        assert result.score == 0.75
        assert result.workers_used == 3
        assert result.duplication_rate == 0.1

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.coordination import CoordinationResult

        result = CoordinationResult(
            score=0.6, reasoning="ok", dimension_scores={},
            workers_used=2, handoffs_completed=1,
            duplication_rate=0.2, merge_conflicts=0,
        )
        d = result.to_dict()
        restored = CoordinationResult.from_dict(d)
        assert restored.score == result.score
        assert restored.workers_used == result.workers_used


# ===========================================================================
# AC-253: CoordinationInterface ABC
# ===========================================================================


class TestCoordinationInterface:
    def test_cannot_instantiate(self) -> None:
        from autocontext.scenarios.coordination import CoordinationInterface

        with pytest.raises(TypeError):
            CoordinationInterface()  # type: ignore[abstract]

    def test_concrete_subclass(self) -> None:
        from autocontext.scenarios.coordination import (
            CoordinationInterface,
            CoordinationResult,
            HandoffRecord,
            WorkerContext,
        )
        from autocontext.scenarios.simulation import (
            Action,
            ActionResult,
            ActionSpec,
            ActionTrace,
            EnvironmentSpec,
            SimulationResult,
        )

        class Stub(CoordinationInterface):
            name = "stub_coord"

            def describe_scenario(self) -> str:
                return "stub"

            def describe_environment(self) -> EnvironmentSpec:
                return EnvironmentSpec(
                    name="stub", description="stub",
                    available_actions=[], initial_state_description="stub",
                    success_criteria=[], failure_modes=[],
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

            def evaluate_trace(
                self, trace: ActionTrace, final_state: dict[str, Any]
            ) -> SimulationResult:
                return SimulationResult(
                    score=0.0, reasoning="", dimension_scores={},
                    workflow_complete=True, actions_taken=0, actions_successful=0,
                )

            def get_rubric(self) -> str:
                return ""

            def max_steps(self) -> int:
                return 5

            def get_worker_contexts(self, state: dict[str, Any]) -> list[WorkerContext]:
                return []

            def get_handoff_log(self, state: dict[str, Any]) -> list[HandoffRecord]:
                return []

            def record_handoff(
                self, state: dict[str, Any], handoff: HandoffRecord
            ) -> dict[str, Any]:
                return state

            def merge_outputs(
                self, state: dict[str, Any], worker_outputs: dict[str, str]
            ) -> dict[str, Any]:
                return state

            def evaluate_coordination(self, state: dict[str, Any]) -> CoordinationResult:
                return CoordinationResult(
                    score=0.5, reasoning="", dimension_scores={},
                    workers_used=0, handoffs_completed=0,
                    duplication_rate=0.0, merge_conflicts=0,
                )

        stub = Stub()
        assert stub.name == "stub_coord"
        assert stub.get_worker_contexts({}) == []
        result = stub.evaluate_coordination({})
        assert isinstance(result, CoordinationResult)


# ===========================================================================
# Family registry integration — both families
# ===========================================================================


class TestFamilyRegistration:
    def test_operator_loop_registered(self) -> None:
        from autocontext.scenarios.families import FAMILY_REGISTRY

        assert "operator_loop" in FAMILY_REGISTRY

    def test_coordination_registered(self) -> None:
        from autocontext.scenarios.families import FAMILY_REGISTRY

        assert "coordination" in FAMILY_REGISTRY

    def test_operator_loop_marker(self) -> None:
        from autocontext.scenarios.families import get_family_marker

        assert get_family_marker("operator_loop") == "operator_loop"

    def test_coordination_marker(self) -> None:
        from autocontext.scenarios.families import get_family_marker

        assert get_family_marker("coordination") == "coordination"

    def test_detect_operator_loop(self) -> None:
        from autocontext.scenarios.families import detect_family
        from autocontext.scenarios.operator_loop import OperatorLoopInterface
        from autocontext.scenarios.simulation import (
            Action,
            ActionResult,
            ActionSpec,
            ActionTrace,
            EnvironmentSpec,
            SimulationResult,
        )

        class Mini(OperatorLoopInterface):
            name = "mini_op"

            def describe_scenario(self) -> str:
                return ""

            def describe_environment(self) -> EnvironmentSpec:
                return EnvironmentSpec(
                    name="", description="", available_actions=[],
                    initial_state_description="", success_criteria=[], failure_modes=[],
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

            def get_escalation_log(self, state: dict[str, Any]) -> list:
                return []

            def get_clarification_log(self, state: dict[str, Any]) -> list:
                return []

            def escalate(self, state: dict[str, Any], event: Any) -> dict[str, Any]:
                return state

            def request_clarification(self, state: dict[str, Any], request: Any) -> dict[str, Any]:
                return state

            def evaluate_judgment(self, state: dict[str, Any]) -> Any:
                from autocontext.scenarios.operator_loop import OperatorLoopResult
                return OperatorLoopResult(
                    score=0.0, reasoning="", dimension_scores={},
                    total_actions=0, escalations=0, necessary_escalations=0,
                    unnecessary_escalations=0, missed_escalations=0,
                    clarifications_requested=0,
                )

        family = detect_family(Mini())
        assert family is not None
        assert family.name == "operator_loop"


# ===========================================================================
# Pipeline registry — both families
# ===========================================================================


class TestPipelineRegistration:
    def test_operator_loop_pipeline_registered(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import PIPELINE_REGISTRY

        assert "operator_loop" in PIPELINE_REGISTRY

    def test_coordination_pipeline_registered(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import PIPELINE_REGISTRY

        assert "coordination" in PIPELINE_REGISTRY

    def test_operator_loop_spec_valid(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec = {
            "description": "Customer support escalation",
            "environment_description": "Support system",
            "initial_state_description": "Ticket received",
            "escalation_policy": {
                "escalation_threshold": "high",
                "max_escalations": 3,
            },
            "success_criteria": ["resolve or correctly escalate"],
            "failure_modes": ["over-escalation"],
            "actions": [{"name": "respond", "description": "d"}],
        }
        errors = validate_for_family("operator_loop", spec)
        assert errors == []

    def test_coordination_spec_valid(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec = {
            "description": "Multi-agent report writing",
            "environment_description": "Research team",
            "initial_state_description": "Task assigned to workers",
            "workers": [
                {"worker_id": "w1", "role": "researcher"},
                {"worker_id": "w2", "role": "writer"},
            ],
            "success_criteria": ["coherent merged report"],
            "failure_modes": ["duplication"],
            "actions": [{"name": "research", "description": "d"}],
        }
        errors = validate_for_family("coordination", spec)
        assert errors == []

    def test_operator_loop_spec_missing_fields(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        errors = validate_for_family("operator_loop", {"description": "x"})
        assert len(errors) > 0

    def test_coordination_spec_missing_fields(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        errors = validate_for_family("coordination", {"description": "x"})
        assert len(errors) > 0


# ===========================================================================
# Classifier routing
# ===========================================================================


class TestClassifierRouting:
    def test_route_operator_loop(self) -> None:
        from autocontext.scenarios.custom.family_classifier import (
            classify_scenario_family,
            route_to_family,
        )

        classification = classify_scenario_family(
            "Agent must decide when to escalate to an operator and "
            "when to request clarification before acting autonomously"
        )
        family = route_to_family(classification)
        assert family.name == "operator_loop"

    def test_route_coordination(self) -> None:
        from autocontext.scenarios.custom.family_classifier import (
            classify_scenario_family,
            route_to_family,
        )

        classification = classify_scenario_family(
            "Multiple worker agents coordinate under partial context "
            "with handoff and merge of outputs"
        )
        family = route_to_family(classification)
        assert family.name == "coordination"


# ===========================================================================
# Designer/spec parsing
# ===========================================================================


class TestOperatorLoopDesigner:
    def test_parse_spec(self) -> None:
        from autocontext.scenarios.custom.operator_loop_designer import (
            OPERATOR_LOOP_SPEC_END,
            OPERATOR_LOOP_SPEC_START,
            parse_operator_loop_spec,
        )

        raw = f"""{OPERATOR_LOOP_SPEC_START}
{{
    "description": "Support triage",
    "environment_description": "Help desk",
    "initial_state_description": "Ticket open",
    "escalation_policy": {{
        "escalation_threshold": "high",
        "max_escalations": 3
    }},
    "success_criteria": ["resolve or escalate correctly"],
    "failure_modes": ["over-escalation"],
    "max_steps": 8,
    "actions": [
        {{
            "name": "respond", "description": "reply to customer",
            "parameters": {{}}, "preconditions": [], "effects": ["replied"]
        }},
        {{
            "name": "escalate_ticket", "description": "escalate to human",
            "parameters": {{}}, "preconditions": [], "effects": ["escalated"]
        }}
    ]
}}
{OPERATOR_LOOP_SPEC_END}"""
        spec = parse_operator_loop_spec(raw)
        assert spec.description == "Support triage"
        assert spec.escalation_policy["max_escalations"] == 3
        assert len(spec.actions) == 2

    def test_design_fn_calls_llm(self) -> None:
        import json

        from autocontext.scenarios.custom.operator_loop_designer import (
            OPERATOR_LOOP_SPEC_END,
            OPERATOR_LOOP_SPEC_START,
            design_operator_loop,
        )

        fake_spec = {
            "description": "test",
            "environment_description": "env",
            "initial_state_description": "init",
            "escalation_policy": {
                "escalation_threshold": "medium",
                "max_escalations": 2,
            },
            "success_criteria": ["ok"],
            "failure_modes": [],
            "max_steps": 6,
            "actions": [
                {
                    "name": "act", "description": "a",
                    "parameters": {}, "preconditions": [], "effects": [],
                }
            ],
        }

        def fake_llm(system: str, user: str) -> str:
            return (
                f"{OPERATOR_LOOP_SPEC_START}\n"
                f"{json.dumps(fake_spec)}\n"
                f"{OPERATOR_LOOP_SPEC_END}"
            )

        spec = design_operator_loop("test", fake_llm)
        assert spec.description == "test"


class TestCoordinationDesigner:
    def test_parse_spec(self) -> None:
        from autocontext.scenarios.custom.coordination_designer import (
            COORDINATION_SPEC_END,
            COORDINATION_SPEC_START,
            parse_coordination_spec,
        )

        raw = f"""{COORDINATION_SPEC_START}
{{
    "description": "Team report",
    "environment_description": "Research team",
    "initial_state_description": "Tasks assigned",
    "workers": [
        {{"worker_id": "w1", "role": "researcher"}},
        {{"worker_id": "w2", "role": "writer"}}
    ],
    "success_criteria": ["merged report"],
    "failure_modes": ["duplication"],
    "max_steps": 10,
    "actions": [
        {{
            "name": "research", "description": "gather data",
            "parameters": {{}}, "preconditions": [], "effects": ["data_gathered"]
        }},
        {{
            "name": "write", "description": "write section",
            "parameters": {{}}, "preconditions": ["research"],
            "effects": ["section_written"]
        }}
    ]
}}
{COORDINATION_SPEC_END}"""
        spec = parse_coordination_spec(raw)
        assert spec.description == "Team report"
        assert len(spec.workers) == 2
        assert spec.workers[0]["worker_id"] == "w1"

    def test_design_fn_calls_llm(self) -> None:
        import json

        from autocontext.scenarios.custom.coordination_designer import (
            COORDINATION_SPEC_END,
            COORDINATION_SPEC_START,
            design_coordination,
        )

        fake_spec = {
            "description": "test",
            "environment_description": "env",
            "initial_state_description": "init",
            "workers": [{"worker_id": "w1", "role": "r"}],
            "success_criteria": ["ok"],
            "failure_modes": [],
            "max_steps": 6,
            "actions": [
                {
                    "name": "work", "description": "w",
                    "parameters": {}, "preconditions": [], "effects": [],
                }
            ],
        }

        def fake_llm(system: str, user: str) -> str:
            return (
                f"{COORDINATION_SPEC_START}\n"
                f"{json.dumps(fake_spec)}\n"
                f"{COORDINATION_SPEC_END}"
            )

        spec = design_coordination("test", fake_llm)
        assert spec.description == "test"


# ===========================================================================
# Codegen
# ===========================================================================


class TestOperatorLoopCodegen:
    def test_runtime_codegen_generates_loadable_source(self) -> None:
        from autocontext.scenarios.custom.operator_loop_codegen import (
            generate_operator_loop_class,
        )
        from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec
        from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

        spec = OperatorLoopSpec(
            description="test",
            environment_description="env",
            initial_state_description="init",
            escalation_policy={"escalation_threshold": "high", "max_escalations": 3},
            success_criteria=["done"],
            failure_modes=[],
            actions=[
                SimulationActionSpecModel(
                    name="act", description="a", parameters={},
                    preconditions=[], effects=[],
                ),
            ],
        )
        source = generate_operator_loop_class(spec, name="test_op")
        ast.parse(source)
        assert "class TestOpOperatorLoop(OperatorLoopInterface):" in source
        assert "def get_escalation_log(" in source
        assert "def evaluate_judgment(" in source


class TestCoordinationCodegen:
    def test_generate_class(self) -> None:
        from autocontext.scenarios.custom.coordination_codegen import generate_coordination_class
        from autocontext.scenarios.custom.coordination_spec import CoordinationSpec
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family
        from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

        spec = CoordinationSpec(
            description="test",
            environment_description="env",
            initial_state_description="init",
            workers=[{"worker_id": "w1", "role": "r"}],
            success_criteria=["done"],
            failure_modes=[],
            actions=[
                SimulationActionSpecModel(
                    name="work", description="w", parameters={},
                    preconditions=[], effects=[],
                ),
            ],
        )
        source = generate_coordination_class(spec, name="test_coord")
        errors = validate_source_for_family("coordination", source)
        assert errors == [], f"validation errors: {errors}"


# ===========================================================================
# Creator end-to-end
# ===========================================================================


class TestOperatorLoopCreator:
    def test_create_persists_and_registers(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.custom.operator_loop_creator import OperatorLoopCreator
        from autocontext.scenarios.custom.operator_loop_designer import (
            OPERATOR_LOOP_SPEC_END,
            OPERATOR_LOOP_SPEC_START,
        )
        from autocontext.scenarios.operator_loop import OperatorLoopInterface

        def fake_llm(system: str, user: str) -> str:
            spec = {
                "description": "test operator loop",
                "environment_description": "support queue",
                "initial_state_description": "tickets pending",
                "escalation_policy": {"escalation_threshold": "high", "max_escalations": 3},
                "success_criteria": ["good escalation judgment"],
                "failure_modes": ["missed escalation"],
                "max_steps": 8,
                "actions": [
                    {
                        "name": "triage_ticket",
                        "description": "triage the next ticket",
                        "parameters": {},
                        "preconditions": [],
                        "effects": ["triaged"],
                    }
                ],
            }
            return (
                f"{OPERATOR_LOOP_SPEC_START}\n"
                f"{json.dumps(spec)}\n"
                f"{OPERATOR_LOOP_SPEC_END}"
            )

        creator = OperatorLoopCreator(fake_llm, tmp_path)
        scenario = creator.create("test", name="test_op_creator")
        assert isinstance(scenario, OperatorLoopInterface)

        scenario_dir = tmp_path / "_custom_scenarios" / "test_op_creator"
        assert (scenario_dir / "scenario.py").exists()
        assert (scenario_dir / "spec.json").exists()
        assert (scenario_dir / "scenario_type.txt").read_text().strip() == "operator_loop"


class TestCoordinationCreator:
    def test_create_and_persist(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.coordination import CoordinationInterface
        from autocontext.scenarios.custom.coordination_creator import CoordinationCreator
        from autocontext.scenarios.custom.coordination_designer import (
            COORDINATION_SPEC_END,
            COORDINATION_SPEC_START,
        )

        fake_spec = {
            "description": "test",
            "environment_description": "env",
            "initial_state_description": "init",
            "workers": [{"worker_id": "w1", "role": "r"}],
            "success_criteria": ["done"],
            "failure_modes": [],
            "max_steps": 6,
            "actions": [
                {
                    "name": "work", "description": "w",
                    "parameters": {}, "preconditions": [], "effects": [],
                }
            ],
        }

        def fake_llm(system: str, user: str) -> str:
            return (
                f"{COORDINATION_SPEC_START}\n"
                f"{json.dumps(fake_spec)}\n"
                f"{COORDINATION_SPEC_END}"
            )

        creator = CoordinationCreator(fake_llm, tmp_path)
        scenario = creator.create("test", name="test_coord_creator")
        assert isinstance(scenario, CoordinationInterface)

        scenario_dir = tmp_path / "_custom_scenarios" / "test_coord_creator"
        assert (scenario_dir / "scenario.py").exists()
        assert (scenario_dir / "scenario_type.txt").read_text().strip() == "coordination"


# ===========================================================================
# AgentTaskCreator routing
# ===========================================================================


class TestAgentTaskCreatorRouting:
    def test_routes_to_operator_loop(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
        from autocontext.scenarios.custom.operator_loop_designer import (
            OPERATOR_LOOP_SPEC_END,
            OPERATOR_LOOP_SPEC_START,
        )
        from autocontext.scenarios.operator_loop import OperatorLoopInterface

        def fake_llm(system: str, user: str) -> str:
            spec = {
                "description": "operator loop routing test",
                "environment_description": "incident console",
                "initial_state_description": "alerts firing",
                "escalation_policy": {"escalation_threshold": "critical", "max_escalations": 2},
                "success_criteria": ["escalate appropriately"],
                "failure_modes": ["over-escalation"],
                "max_steps": 6,
                "actions": [
                    {
                        "name": "inspect_alert",
                        "description": "inspect an alert",
                        "parameters": {},
                        "preconditions": [],
                        "effects": ["inspected"],
                    }
                ],
            }
            return (
                f"{OPERATOR_LOOP_SPEC_START}\n"
                f"{json.dumps(spec)}\n"
                f"{OPERATOR_LOOP_SPEC_END}"
            )

        creator = AgentTaskCreator(fake_llm, tmp_path)
        scenario = creator.create(
            "An operator-in-the-loop scenario where the agent must "
            "decide when to escalate and when to request clarification"
        )
        assert isinstance(scenario, OperatorLoopInterface)

        scenario_dir = tmp_path / "_custom_scenarios" / creator.derive_name(
            "An operator-in-the-loop scenario where the agent must "
            "decide when to escalate and when to request clarification"
        )
        assert (scenario_dir / "scenario.py").exists()
        assert (scenario_dir / "scenario_type.txt").read_text().strip() == "operator_loop"

    def test_routes_to_coordination(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.coordination import CoordinationInterface
        from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
        from autocontext.scenarios.custom.coordination_designer import (
            COORDINATION_SPEC_END,
            COORDINATION_SPEC_START,
        )

        fake_spec = {
            "description": "routing test",
            "environment_description": "env",
            "initial_state_description": "init",
            "workers": [{"worker_id": "w1", "role": "r"}],
            "success_criteria": ["done"],
            "failure_modes": [],
            "max_steps": 6,
            "actions": [
                {
                    "name": "work", "description": "w",
                    "parameters": {}, "preconditions": [], "effects": [],
                }
            ],
        }

        def fake_llm(system: str, user: str) -> str:
            return (
                f"{COORDINATION_SPEC_START}\n"
                f"{json.dumps(fake_spec)}\n"
                f"{COORDINATION_SPEC_END}"
            )

        creator = AgentTaskCreator(fake_llm, tmp_path)
        scenario = creator.create(
            "Multi-agent coordination where worker agents have partial context "
            "and must handoff information and merge outputs"
        )
        assert isinstance(scenario, CoordinationInterface)
