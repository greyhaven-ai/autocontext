"""Tests for AC-249: Investigation and workflow scenario families.

Validates:
- InvestigationInterface ABC and data models (EvidenceItem, EvidenceChain,
  InvestigationResult) for evidence-chain evaluation with red herring detection
  and diagnosis accuracy.
- WorkflowInterface ABC and data models (WorkflowStep, SideEffect,
  CompensationAction, WorkflowResult) for transactional workflow evaluation
  with retry, rollback, compensation, and side-effect tracking.
- Family and pipeline registration for both families.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Investigation data models
# ---------------------------------------------------------------------------


class TestEvidenceItem:
    def test_construction(self) -> None:
        from autocontext.scenarios.investigation import EvidenceItem

        item = EvidenceItem(
            id="ev-001",
            content="Server log shows 503 at 14:03 UTC",
            source="server_logs",
            relevance=0.9,
            is_red_herring=False,
        )
        assert item.id == "ev-001"
        assert item.source == "server_logs"
        assert item.relevance == 0.9
        assert item.is_red_herring is False
        assert item.metadata == {}

    def test_red_herring_item(self) -> None:
        from autocontext.scenarios.investigation import EvidenceItem

        item = EvidenceItem(
            id="ev-002",
            content="Unrelated cron job ran at 14:01 UTC",
            source="cron_logs",
            relevance=0.1,
            is_red_herring=True,
        )
        assert item.is_red_herring is True

    def test_with_metadata(self) -> None:
        from autocontext.scenarios.investigation import EvidenceItem

        item = EvidenceItem(
            id="ev-003",
            content="Memory spike",
            source="metrics",
            relevance=0.7,
            is_red_herring=False,
            metadata={"timestamp": "14:02 UTC", "severity": "high"},
        )
        assert item.metadata["severity"] == "high"

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.investigation import EvidenceItem

        item = EvidenceItem(
            id="ev-001",
            content="Log entry",
            source="logs",
            relevance=0.8,
            is_red_herring=False,
            metadata={"line": 42},
        )
        data = item.to_dict()
        restored = EvidenceItem.from_dict(data)
        assert restored.id == item.id
        assert restored.content == item.content
        assert restored.relevance == item.relevance
        assert restored.metadata == item.metadata


class TestEvidenceChain:
    def test_construction(self) -> None:
        from autocontext.scenarios.investigation import EvidenceChain, EvidenceItem

        chain = EvidenceChain(
            items=[
                EvidenceItem(id="1", content="a", source="s", relevance=0.9, is_red_herring=False),
                EvidenceItem(id="2", content="b", source="s", relevance=0.7, is_red_herring=False),
            ],
            reasoning="a caused b",
        )
        assert len(chain.items) == 2
        assert chain.reasoning == "a caused b"

    def test_contains_red_herring(self) -> None:
        from autocontext.scenarios.investigation import EvidenceChain, EvidenceItem

        chain = EvidenceChain(
            items=[
                EvidenceItem(id="1", content="real", source="s", relevance=0.9, is_red_herring=False),
                EvidenceItem(id="2", content="trap", source="s", relevance=0.3, is_red_herring=True),
            ],
            reasoning="mixed",
        )
        assert chain.contains_red_herring is True

    def test_no_red_herring(self) -> None:
        from autocontext.scenarios.investigation import EvidenceChain, EvidenceItem

        chain = EvidenceChain(
            items=[
                EvidenceItem(id="1", content="real", source="s", relevance=0.9, is_red_herring=False),
            ],
            reasoning="clean",
        )
        assert chain.contains_red_herring is False

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.investigation import EvidenceChain, EvidenceItem

        chain = EvidenceChain(
            items=[
                EvidenceItem(id="1", content="x", source="s", relevance=0.5, is_red_herring=False),
            ],
            reasoning="because",
        )
        data = chain.to_dict()
        restored = EvidenceChain.from_dict(data)
        assert len(restored.items) == 1
        assert restored.reasoning == "because"


class TestInvestigationResult:
    def test_construction(self) -> None:
        from autocontext.scenarios.investigation import InvestigationResult

        result = InvestigationResult(
            score=0.85,
            reasoning="Good investigation",
            dimension_scores={"evidence_quality": 0.9, "diagnosis_accuracy": 0.8},
            diagnosis="Memory leak in auth service",
            evidence_collected=5,
            red_herrings_avoided=2,
            red_herrings_followed=0,
            diagnosis_correct=True,
        )
        assert result.score == 0.85
        assert result.diagnosis_correct is True
        assert result.red_herrings_avoided == 2

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.investigation import InvestigationResult

        result = InvestigationResult(
            score=0.7,
            reasoning="Partial",
            dimension_scores={"evidence_quality": 0.6},
            diagnosis="Disk full",
            evidence_collected=3,
            red_herrings_avoided=1,
            red_herrings_followed=1,
            diagnosis_correct=False,
        )
        data = result.to_dict()
        restored = InvestigationResult.from_dict(data)
        assert restored.score == result.score
        assert restored.diagnosis == result.diagnosis
        assert restored.red_herrings_followed == 1
        assert restored.diagnosis_correct is False


# ---------------------------------------------------------------------------
# InvestigationInterface ABC
# ---------------------------------------------------------------------------


class TestInvestigationInterfaceABC:
    def test_cannot_instantiate_abc(self) -> None:
        from autocontext.scenarios.investigation import InvestigationInterface

        with pytest.raises(TypeError, match="abstract"):
            InvestigationInterface()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        from autocontext.scenarios.investigation import (
            EvidenceChain,
            EvidenceItem,
            InvestigationInterface,
            InvestigationResult,
        )

        class _MockInvestigation(InvestigationInterface):
            name = "mock_investigation"

            def describe_scenario(self) -> str:
                return "Investigate the outage"

            def describe_environment(self) -> Any:
                from autocontext.scenarios.simulation import ActionSpec, EnvironmentSpec

                return EnvironmentSpec(
                    name="mock_investigation",
                    description="Server environment",
                    available_actions=[
                        ActionSpec(name="examine_logs", description="Check logs", parameters={}),
                        ActionSpec(name="query_metrics", description="Check metrics", parameters={}),
                    ],
                    initial_state_description="Outage detected",
                    success_criteria=["Root cause identified"],
                )

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"phase": "initial", "evidence": [], "seed": seed or 0}

            def get_available_actions(self, state: dict[str, Any]) -> list:
                return self.describe_environment().available_actions

            def execute_action(self, state: dict[str, Any], action: Any) -> tuple:
                from autocontext.scenarios.simulation import ActionResult

                return ActionResult(success=True, output="data", state_changes={}), state

            def is_terminal(self, state: Any) -> bool:
                return state.get("diagnosed", False)

            def evaluate_trace(self, trace: Any, final_state: dict[str, Any]) -> Any:
                from autocontext.scenarios.simulation import SimulationResult

                return SimulationResult(
                    score=1.0, reasoning="ok", dimension_scores={},
                    workflow_complete=True, actions_taken=0, actions_successful=0,
                )

            def get_rubric(self) -> str:
                return "Evidence quality, diagnosis accuracy"

            def get_evidence_pool(self, state: dict[str, Any]) -> list[EvidenceItem]:
                return [
                    EvidenceItem(id="1", content="log entry", source="logs", relevance=0.9, is_red_herring=False),
                    EvidenceItem(id="2", content="noise", source="cron", relevance=0.1, is_red_herring=True),
                ]

            def evaluate_evidence_chain(
                self, chain: EvidenceChain, state: dict[str, Any]
            ) -> float:
                return 0.8

            def evaluate_diagnosis(
                self, diagnosis: str, evidence_chain: EvidenceChain, state: dict[str, Any]
            ) -> InvestigationResult:
                return InvestigationResult(
                    score=0.9, reasoning="Correct", dimension_scores={"accuracy": 0.9},
                    diagnosis=diagnosis, evidence_collected=len(evidence_chain.items),
                    red_herrings_avoided=1, red_herrings_followed=0, diagnosis_correct=True,
                )

        inv = _MockInvestigation()
        assert inv.name == "mock_investigation"

    def test_describe_scenario(self) -> None:
        inv = self._make_mock()
        assert "outage" in inv.describe_scenario().lower() or "investigate" in inv.describe_scenario().lower()

    def test_get_evidence_pool(self) -> None:
        inv = self._make_mock()
        pool = inv.get_evidence_pool(inv.initial_state())
        assert len(pool) >= 1
        assert any(not e.is_red_herring for e in pool)

    def test_get_evidence_pool_contains_red_herring(self) -> None:
        inv = self._make_mock()
        pool = inv.get_evidence_pool(inv.initial_state())
        assert any(e.is_red_herring for e in pool)

    def test_evaluate_evidence_chain(self) -> None:
        from autocontext.scenarios.investigation import EvidenceChain, EvidenceItem

        inv = self._make_mock()
        chain = EvidenceChain(
            items=[EvidenceItem(id="1", content="log", source="s", relevance=0.9, is_red_herring=False)],
            reasoning="caused by",
        )
        score = inv.evaluate_evidence_chain(chain, inv.initial_state())
        assert 0.0 <= score <= 1.0

    def test_evaluate_diagnosis(self) -> None:
        from autocontext.scenarios.investigation import EvidenceChain, EvidenceItem

        inv = self._make_mock()
        chain = EvidenceChain(
            items=[EvidenceItem(id="1", content="log", source="s", relevance=0.9, is_red_herring=False)],
            reasoning="root cause",
        )
        result = inv.evaluate_diagnosis("Memory leak", chain, inv.initial_state())
        assert result.score >= 0.0
        assert isinstance(result.diagnosis_correct, bool)

    def test_initial_state(self) -> None:
        inv = self._make_mock()
        state = inv.initial_state(seed=42)
        assert isinstance(state, dict)

    def _make_mock(self) -> Any:
        """Helper to build a mock investigation for non-ABC tests."""
        from autocontext.scenarios.investigation import (
            EvidenceChain,
            EvidenceItem,
            InvestigationInterface,
            InvestigationResult,
        )

        class _M(InvestigationInterface):
            name = "mock_inv"

            def describe_scenario(self) -> str:
                return "Investigate the outage"

            def describe_environment(self) -> Any:
                from autocontext.scenarios.simulation import ActionSpec, EnvironmentSpec

                return EnvironmentSpec(
                    name="mock_inv", description="env",
                    available_actions=[ActionSpec(name="check", description="d", parameters={})],
                    initial_state_description="start", success_criteria=["done"],
                )

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"evidence": [], "seed": seed or 0}

            def get_available_actions(self, state: dict[str, Any]) -> list:
                return self.describe_environment().available_actions

            def execute_action(self, state: dict[str, Any], action: Any) -> tuple:
                from autocontext.scenarios.simulation import ActionResult

                return ActionResult(success=True, output="ok", state_changes={}), state

            def is_terminal(self, state: Any) -> bool:
                return False

            def evaluate_trace(self, trace: Any, final_state: dict[str, Any]) -> Any:
                from autocontext.scenarios.simulation import SimulationResult

                return SimulationResult(
                    score=1.0, reasoning="ok", dimension_scores={},
                    workflow_complete=True, actions_taken=0, actions_successful=0,
                )

            def get_rubric(self) -> str:
                return "rubric"

            def get_evidence_pool(self, state: dict[str, Any]) -> list[EvidenceItem]:
                return [
                    EvidenceItem(id="1", content="log", source="s", relevance=0.9, is_red_herring=False),
                    EvidenceItem(id="2", content="noise", source="s", relevance=0.1, is_red_herring=True),
                ]

            def evaluate_evidence_chain(self, chain: EvidenceChain, state: dict[str, Any]) -> float:
                return 0.8

            def evaluate_diagnosis(
                self, diagnosis: str, evidence_chain: EvidenceChain, state: dict[str, Any]
            ) -> InvestigationResult:
                return InvestigationResult(
                    score=0.9, reasoning="Good", dimension_scores={"accuracy": 0.9},
                    diagnosis=diagnosis, evidence_collected=len(evidence_chain.items),
                    red_herrings_avoided=1, red_herrings_followed=0, diagnosis_correct=True,
                )

        return _M()


# ---------------------------------------------------------------------------
# Workflow data models
# ---------------------------------------------------------------------------


class TestWorkflowStep:
    def test_construction(self) -> None:
        from autocontext.scenarios.workflow import WorkflowStep

        step = WorkflowStep(
            name="charge_payment",
            description="Charge the customer's card",
            idempotent=False,
            reversible=True,
            compensation="refund_payment",
        )
        assert step.name == "charge_payment"
        assert step.idempotent is False
        assert step.reversible is True
        assert step.compensation == "refund_payment"

    def test_non_reversible(self) -> None:
        from autocontext.scenarios.workflow import WorkflowStep

        step = WorkflowStep(
            name="send_email",
            description="Send confirmation email",
            idempotent=True,
            reversible=False,
        )
        assert step.reversible is False
        assert step.compensation is None

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.workflow import WorkflowStep

        step = WorkflowStep(
            name="reserve_inventory",
            description="Reserve items",
            idempotent=True,
            reversible=True,
            compensation="release_inventory",
        )
        data = step.to_dict()
        restored = WorkflowStep.from_dict(data)
        assert restored.name == step.name
        assert restored.compensation == step.compensation
        assert restored.idempotent == step.idempotent


class TestSideEffect:
    def test_construction(self) -> None:
        from autocontext.scenarios.workflow import SideEffect

        se = SideEffect(
            step_name="charge_payment",
            effect_type="external_api",
            description="Payment gateway charged $50",
            reversible=True,
            reversed=False,
        )
        assert se.step_name == "charge_payment"
        assert se.effect_type == "external_api"
        assert se.reversed is False

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.workflow import SideEffect

        se = SideEffect(
            step_name="send_sms",
            effect_type="notification",
            description="SMS sent",
            reversible=False,
            reversed=False,
        )
        data = se.to_dict()
        restored = SideEffect.from_dict(data)
        assert restored.step_name == se.step_name
        assert restored.reversible is False


class TestCompensationAction:
    def test_construction(self) -> None:
        from autocontext.scenarios.workflow import CompensationAction

        comp = CompensationAction(
            step_name="charge_payment",
            compensation_name="refund_payment",
            success=True,
            output="Refund processed",
        )
        assert comp.step_name == "charge_payment"
        assert comp.success is True

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.workflow import CompensationAction

        comp = CompensationAction(
            step_name="reserve",
            compensation_name="release",
            success=False,
            output="Release failed",
        )
        data = comp.to_dict()
        restored = CompensationAction.from_dict(data)
        assert restored.success is False
        assert restored.output == "Release failed"


class TestWorkflowResult:
    def test_construction(self) -> None:
        from autocontext.scenarios.workflow import SideEffect, WorkflowResult

        result = WorkflowResult(
            score=0.75,
            reasoning="Partial workflow completed",
            dimension_scores={"completeness": 0.8, "compensation_quality": 0.7},
            steps_completed=3,
            steps_total=5,
            retries=1,
            compensations_triggered=1,
            compensations_successful=1,
            side_effects=[
                SideEffect(
                    step_name="charge", effect_type="payment", description="charged",
                    reversible=True, reversed=True,
                ),
            ],
            side_effects_reversed=1,
            side_effects_leaked=0,
        )
        assert result.score == 0.75
        assert result.steps_completed == 3
        assert result.side_effects_leaked == 0

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.workflow import WorkflowResult

        result = WorkflowResult(
            score=0.5,
            reasoning="Poor",
            dimension_scores={"completeness": 0.3},
            steps_completed=1,
            steps_total=4,
            retries=2,
            compensations_triggered=2,
            compensations_successful=1,
            side_effects=[],
            side_effects_reversed=0,
            side_effects_leaked=1,
        )
        data = result.to_dict()
        restored = WorkflowResult.from_dict(data)
        assert restored.score == result.score
        assert restored.retries == 2
        assert restored.side_effects_leaked == 1


# ---------------------------------------------------------------------------
# WorkflowInterface ABC
# ---------------------------------------------------------------------------


class TestWorkflowInterfaceABC:
    def test_cannot_instantiate_abc(self) -> None:
        from autocontext.scenarios.workflow import WorkflowInterface

        with pytest.raises(TypeError, match="abstract"):
            WorkflowInterface()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        wf = self._make_mock()
        assert wf.name == "mock_workflow"

    def test_describe_scenario(self) -> None:
        wf = self._make_mock()
        assert isinstance(wf.describe_scenario(), str)

    def test_get_workflow_steps(self) -> None:
        from autocontext.scenarios.workflow import WorkflowStep

        wf = self._make_mock()
        steps = wf.get_workflow_steps()
        assert len(steps) >= 1
        assert all(isinstance(s, WorkflowStep) for s in steps)

    def test_get_workflow_steps_has_compensation(self) -> None:
        wf = self._make_mock()
        steps = wf.get_workflow_steps()
        assert any(s.compensation is not None for s in steps)

    def test_execute_step(self) -> None:
        from autocontext.scenarios.simulation import ActionResult

        wf = self._make_mock()
        state = wf.initial_state()
        steps = wf.get_workflow_steps()
        result, new_state = wf.execute_step(state, steps[0])
        assert isinstance(result, ActionResult)
        assert isinstance(new_state, dict)

    def test_execute_compensation(self) -> None:
        from autocontext.scenarios.workflow import CompensationAction

        wf = self._make_mock()
        state = wf.initial_state()
        steps = wf.get_workflow_steps()
        reversible_step = next(s for s in steps if s.compensation)
        comp = wf.execute_compensation(state, reversible_step)
        assert isinstance(comp, CompensationAction)

    def test_get_side_effects(self) -> None:
        wf = self._make_mock()
        state = wf.initial_state()
        side_effects = wf.get_side_effects(state)
        assert isinstance(side_effects, list)

    def test_evaluate_workflow(self) -> None:
        from autocontext.scenarios.workflow import WorkflowResult

        wf = self._make_mock()
        state = wf.initial_state()
        result = wf.evaluate_workflow(state)
        assert isinstance(result, WorkflowResult)
        assert 0.0 <= result.score <= 1.0

    def test_initial_state(self) -> None:
        wf = self._make_mock()
        state = wf.initial_state(seed=42)
        assert isinstance(state, dict)

    def _make_mock(self) -> Any:
        from autocontext.scenarios.simulation import ActionResult, ActionSpec, EnvironmentSpec
        from autocontext.scenarios.workflow import (
            CompensationAction,
            SideEffect,
            WorkflowInterface,
            WorkflowResult,
            WorkflowStep,
        )

        class _M(WorkflowInterface):
            name = "mock_workflow"

            def describe_scenario(self) -> str:
                return "Process an order with payment and inventory"

            def describe_environment(self) -> EnvironmentSpec:
                return EnvironmentSpec(
                    name="mock_workflow", description="order processing",
                    available_actions=[ActionSpec(name="charge", description="charge card", parameters={})],
                    initial_state_description="order pending", success_criteria=["order fulfilled"],
                )

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"phase": "pending", "seed": seed or 0, "completed_steps": [], "side_effects": []}

            def get_available_actions(self, state: dict[str, Any]) -> list:
                return self.describe_environment().available_actions

            def execute_action(self, state: dict[str, Any], action: Any) -> tuple:
                return ActionResult(success=True, output="ok", state_changes={}), state

            def is_terminal(self, state: Any) -> bool:
                return state.get("phase") == "complete"

            def evaluate_trace(self, trace: Any, final_state: dict[str, Any]) -> Any:
                from autocontext.scenarios.simulation import SimulationResult

                return SimulationResult(
                    score=1.0, reasoning="ok", dimension_scores={},
                    workflow_complete=True, actions_taken=0, actions_successful=0,
                )

            def get_rubric(self) -> str:
                return "Completeness, compensation, side effects"

            def get_workflow_steps(self) -> list[WorkflowStep]:
                return [
                    WorkflowStep(
                        name="charge_payment", description="Charge card",
                        idempotent=False, reversible=True, compensation="refund_payment",
                    ),
                    WorkflowStep(
                        name="reserve_inventory", description="Reserve items",
                        idempotent=True, reversible=True, compensation="release_inventory",
                    ),
                    WorkflowStep(
                        name="send_confirmation", description="Send email",
                        idempotent=True, reversible=False,
                    ),
                ]

            def execute_step(self, state: dict[str, Any], step: WorkflowStep) -> tuple[ActionResult, dict[str, Any]]:
                new_state = dict(state)
                new_state.setdefault("completed_steps", []).append(step.name)
                return ActionResult(success=True, output=f"{step.name} done", state_changes={}), new_state

            def execute_compensation(self, state: dict[str, Any], step: WorkflowStep) -> CompensationAction:
                return CompensationAction(
                    step_name=step.name,
                    compensation_name=step.compensation or "",
                    success=True,
                    output=f"Compensated {step.name}",
                )

            def get_side_effects(self, state: dict[str, Any]) -> list[SideEffect]:
                return [
                    SideEffect(
                        step_name="charge_payment", effect_type="payment",
                        description="Charged $50", reversible=True, reversed=False,
                    ),
                ]

            def evaluate_workflow(self, state: dict[str, Any]) -> WorkflowResult:
                return WorkflowResult(
                    score=0.9, reasoning="Good", dimension_scores={"completeness": 1.0},
                    steps_completed=3, steps_total=3, retries=0,
                    compensations_triggered=0, compensations_successful=0,
                    side_effects=[], side_effects_reversed=0, side_effects_leaked=0,
                )

        return _M()


# ---------------------------------------------------------------------------
# Family registry integration
# ---------------------------------------------------------------------------


class TestFamilyRegistration:
    def test_investigation_family_registered(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("investigation")
        assert family.name == "investigation"
        assert family.evaluation_mode == "evidence_evaluation"

    def test_investigation_scenario_type_marker(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("investigation")
        assert family.scenario_type_marker == "investigation"

    def test_workflow_family_registered(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("workflow")
        assert family.name == "workflow"
        assert family.evaluation_mode == "workflow_evaluation"

    def test_workflow_scenario_type_marker(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("workflow")
        assert family.scenario_type_marker == "workflow"

    def test_detect_family_investigation(self) -> None:
        from autocontext.scenarios.families import detect_family

        inv = TestInvestigationInterfaceABC()._make_mock()
        family = detect_family(inv)
        assert family is not None
        assert family.name == "investigation"

    def test_detect_family_workflow(self) -> None:
        from autocontext.scenarios.families import detect_family

        wf = TestWorkflowInterfaceABC()._make_mock()
        family = detect_family(wf)
        assert family is not None
        assert family.name == "workflow"


# ---------------------------------------------------------------------------
# Pipeline registry integration
# ---------------------------------------------------------------------------


class TestInvestigationPipeline:
    def test_pipeline_registered(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import has_pipeline

        assert has_pipeline("investigation") is True

    def test_pipeline_spec_validation_valid(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {
            "description": "Investigate production outage",
            "environment_description": "Multi-service infrastructure",
            "initial_state_description": "503 errors detected",
            "evidence_pool_description": "Server logs, metrics, traces",
            "diagnosis_target": "Root cause of outage",
            "success_criteria": ["Root cause identified"],
            "actions": [{"name": "check_logs", "description": "Read logs", "parameters": {}}],
        }
        errors = validate_for_family("investigation", spec)
        assert errors == []

    def test_pipeline_spec_validation_missing_fields(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {"description": "Investigate something"}
        errors = validate_for_family("investigation", spec)
        assert len(errors) > 0
        assert any("evidence_pool_description" in e or "diagnosis_target" in e for e in errors)

    def test_pipeline_source_validation(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
from autocontext.scenarios.investigation import InvestigationInterface

class MyInv(InvestigationInterface):
    name = "my_inv"
    def describe_scenario(self): return "scenario"
    def describe_environment(self): pass
    def initial_state(self, seed=None): return {}
    def get_available_actions(self, state): return []
    def execute_action(self, state, action): pass
    def is_terminal(self, state): return False
    def evaluate_trace(self, trace, final_state): pass
    def get_rubric(self): return "rubric"
    def get_evidence_pool(self, state): return []
    def evaluate_evidence_chain(self, chain, state): return 0.0
    def evaluate_diagnosis(self, diagnosis, chain, state): pass
'''
        errors = validate_source_for_family("investigation", source)
        assert errors == []

    def test_pipeline_source_wrong_base_class(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
class NotAnInvestigation:
    pass
'''
        errors = validate_source_for_family("investigation", source)
        assert any("InvestigationInterface" in e for e in errors)


class TestWorkflowPipeline:
    def test_pipeline_registered(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import has_pipeline

        assert has_pipeline("workflow") is True

    def test_pipeline_spec_validation_valid(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {
            "description": "Process order with payment and fulfillment",
            "environment_description": "E-commerce backend",
            "initial_state_description": "Order placed",
            "workflow_steps": [
                {"name": "charge", "description": "Charge card", "reversible": True, "compensation": "refund"},
            ],
            "success_criteria": ["Order fulfilled"],
            "actions": [{"name": "charge", "description": "Charge card", "parameters": {}}],
        }
        errors = validate_for_family("workflow", spec)
        assert errors == []

    def test_pipeline_spec_validation_missing_fields(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {"description": "Process something"}
        errors = validate_for_family("workflow", spec)
        assert len(errors) > 0
        assert any("workflow_steps" in e for e in errors)

    def test_pipeline_spec_empty_workflow_steps(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {
            "description": "Process order",
            "environment_description": "backend",
            "initial_state_description": "order placed",
            "workflow_steps": [],
            "success_criteria": ["done"],
            "actions": [{"name": "a"}],
        }
        errors = validate_for_family("workflow", spec)
        assert any("workflow_steps" in e and "empty" in e for e in errors)

    def test_pipeline_source_validation(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
from autocontext.scenarios.workflow import WorkflowInterface

class MyWF(WorkflowInterface):
    name = "my_wf"
    def describe_scenario(self): return "scenario"
    def describe_environment(self): pass
    def initial_state(self, seed=None): return {}
    def get_available_actions(self, state): return []
    def execute_action(self, state, action): pass
    def is_terminal(self, state): return False
    def evaluate_trace(self, trace, final_state): pass
    def get_rubric(self): return "rubric"
    def get_workflow_steps(self): return []
    def execute_step(self, state, step): pass
    def execute_compensation(self, state, step): pass
    def get_side_effects(self, state): return []
    def evaluate_workflow(self, state): pass
'''
        errors = validate_source_for_family("workflow", source)
        assert errors == []

    def test_pipeline_source_wrong_base_class(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
class NotAWorkflow:
    pass
'''
        errors = validate_source_for_family("workflow", source)
        assert any("WorkflowInterface" in e for e in errors)


# ---------------------------------------------------------------------------
# Cross-family mismatch
# ---------------------------------------------------------------------------


class TestCrossFamilyMismatch:
    def test_investigation_spec_through_workflow_pipeline(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        inv_spec: dict[str, Any] = {
            "description": "Investigate outage",
            "environment_description": "servers",
            "initial_state_description": "error detected",
            "evidence_pool_description": "logs and metrics",
            "diagnosis_target": "root cause",
            "success_criteria": ["found"],
            "actions": [{"name": "check"}],
        }
        errors = validate_for_family("workflow", inv_spec)
        assert len(errors) > 0, "Investigation spec should fail workflow validation"

    def test_workflow_spec_through_investigation_pipeline(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        wf_spec: dict[str, Any] = {
            "description": "Process order",
            "environment_description": "backend",
            "initial_state_description": "pending",
            "workflow_steps": [{"name": "charge", "description": "d", "reversible": True}],
            "success_criteria": ["done"],
            "actions": [{"name": "charge"}],
        }
        errors = validate_for_family("investigation", wf_spec)
        assert len(errors) > 0, "Workflow spec should fail investigation validation"
