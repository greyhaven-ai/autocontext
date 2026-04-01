"""Tests for AC-252 + AC-254: Schema-evolution and tool-fragility scenario families.

AC-252: SchemaEvolutionInterface — schemas/state change mid-run, agent must
detect stale context and adapt. Scores stale-assumption detection and recovery.

AC-254: ToolFragilityInterface — tools/APIs drift while task stays the same.
Separates routing, instruction, runtime/tool, and stale-context failures.
Scores adaptation quality and wasted attempts.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

# ===========================================================================
# AC-252: Schema-evolution data models
# ===========================================================================


class TestSchemaMutation:
    def test_construction(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaMutation

        m = SchemaMutation(
            version=2,
            description="Add 'priority' field to task schema",
            fields_added=["priority"],
            fields_removed=[],
            fields_modified={},
            breaking=False,
        )
        assert m.version == 2
        assert m.fields_added == ["priority"]
        assert m.breaking is False

    def test_breaking_change(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaMutation

        m = SchemaMutation(
            version=3,
            description="Rename 'status' to 'state'",
            fields_added=["state"],
            fields_removed=["status"],
            fields_modified={},
            breaking=True,
        )
        assert m.breaking is True
        assert "status" in m.fields_removed

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaMutation

        m = SchemaMutation(
            version=2,
            description="desc",
            fields_added=["x"],
            fields_removed=["y"],
            fields_modified={"z": "int -> str"},
            breaking=True,
        )
        data = m.to_dict()
        restored = SchemaMutation.from_dict(data)
        assert restored.version == m.version
        assert restored.fields_added == m.fields_added
        assert restored.fields_modified == m.fields_modified
        assert restored.breaking == m.breaking


class TestContextValidity:
    def test_valid(self) -> None:
        from autocontext.scenarios.schema_evolution import ContextValidity

        cv = ContextValidity(
            assumption="status field exists",
            still_valid=True,
            invalidated_by_version=None,
        )
        assert cv.still_valid is True
        assert cv.invalidated_by_version is None

    def test_invalid(self) -> None:
        from autocontext.scenarios.schema_evolution import ContextValidity

        cv = ContextValidity(
            assumption="status field exists",
            still_valid=False,
            invalidated_by_version=3,
        )
        assert cv.still_valid is False
        assert cv.invalidated_by_version == 3

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.schema_evolution import ContextValidity

        cv = ContextValidity(
            assumption="field X exists",
            still_valid=False,
            invalidated_by_version=2,
        )
        data = cv.to_dict()
        restored = ContextValidity.from_dict(data)
        assert restored.assumption == cv.assumption
        assert restored.still_valid is False
        assert restored.invalidated_by_version == 2


class TestSchemaEvolutionResult:
    def test_construction(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaEvolutionResult

        r = SchemaEvolutionResult(
            score=0.8,
            reasoning="Good adaptation",
            dimension_scores={"detection": 0.9, "recovery": 0.7},
            mutations_applied=3,
            stale_assumptions_detected=2,
            stale_assumptions_missed=1,
            recovery_actions_taken=2,
            recovery_actions_successful=2,
        )
        assert r.score == 0.8
        assert r.stale_assumptions_detected == 2
        assert r.stale_assumptions_missed == 1

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaEvolutionResult

        r = SchemaEvolutionResult(
            score=0.6,
            reasoning="Partial",
            dimension_scores={"detection": 0.5},
            mutations_applied=2,
            stale_assumptions_detected=1,
            stale_assumptions_missed=2,
            recovery_actions_taken=1,
            recovery_actions_successful=0,
        )
        data = r.to_dict()
        restored = SchemaEvolutionResult.from_dict(data)
        assert restored.score == r.score
        assert restored.mutations_applied == 2
        assert restored.stale_assumptions_missed == 2


# ===========================================================================
# AC-252: SchemaEvolutionInterface ABC
# ===========================================================================


class TestSchemaEvolutionInterfaceABC:
    def test_cannot_instantiate_abc(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface

        with pytest.raises(TypeError, match="abstract"):
            SchemaEvolutionInterface()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        mock = self._make_mock()
        assert mock.name == "mock_schema_evo"

    def test_describe_scenario(self) -> None:
        mock = self._make_mock()
        assert isinstance(mock.describe_scenario(), str)

    def test_get_schema_version(self) -> None:
        mock = self._make_mock()
        state = mock.initial_state()
        version = mock.get_schema_version(state)
        assert isinstance(version, int)
        assert version >= 1

    def test_get_mutation_log(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaMutation

        mock = self._make_mock()
        state = mock.initial_state()
        log = mock.get_mutation_log(state)
        assert isinstance(log, list)
        assert all(isinstance(m, SchemaMutation) for m in log)

    def test_apply_mutation(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaMutation

        mock = self._make_mock()
        state = mock.initial_state()
        mutation = SchemaMutation(
            version=2, description="add field",
            fields_added=["priority"], fields_removed=[],
            fields_modified={}, breaking=False,
        )
        new_state = mock.apply_mutation(state, mutation)
        assert isinstance(new_state, dict)
        assert mock.get_schema_version(new_state) == 2

    def test_check_context_validity(self) -> None:
        from autocontext.scenarios.schema_evolution import ContextValidity

        mock = self._make_mock()
        state = mock.initial_state()
        results = mock.check_context_validity(state, ["status field exists"])
        assert isinstance(results, list)
        assert all(isinstance(cv, ContextValidity) for cv in results)

    def test_evaluate_adaptation(self) -> None:
        from autocontext.scenarios.schema_evolution import SchemaEvolutionResult

        mock = self._make_mock()
        state = mock.initial_state()
        result = mock.evaluate_adaptation(state)
        assert isinstance(result, SchemaEvolutionResult)
        assert 0.0 <= result.score <= 1.0

    def test_initial_state(self) -> None:
        mock = self._make_mock()
        state = mock.initial_state(seed=42)
        assert isinstance(state, dict)

    def _make_mock(self) -> Any:
        from autocontext.scenarios.schema_evolution import (
            ContextValidity,
            SchemaEvolutionInterface,
            SchemaEvolutionResult,
            SchemaMutation,
        )
        from autocontext.scenarios.simulation import ActionResult, ActionSpec, EnvironmentSpec

        class _M(SchemaEvolutionInterface):
            name = "mock_schema_evo"

            def describe_scenario(self) -> str:
                return "Schema changes mid-run"

            def describe_environment(self) -> EnvironmentSpec:
                return EnvironmentSpec(
                    name="mock_schema_evo", description="evolving schema",
                    available_actions=[ActionSpec(name="query", description="query data", parameters={})],
                    initial_state_description="v1 schema", success_criteria=["adapted to v3"],
                )

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"schema_version": 1, "mutations": [], "seed": seed or 0}

            def get_available_actions(self, state: dict[str, Any]) -> list:
                return self.describe_environment().available_actions

            def execute_action(self, state: dict[str, Any], action: Any) -> tuple:
                return ActionResult(success=True, output="ok", state_changes={}), state

            def is_terminal(self, state: Mapping[str, Any]) -> bool:
                return bool(state.get("schema_version", 1) >= 3)

            def evaluate_trace(self, trace: Any, final_state: dict[str, Any]) -> Any:
                from autocontext.scenarios.simulation import SimulationResult

                return SimulationResult(
                    score=1.0, reasoning="ok", dimension_scores={},
                    workflow_complete=True, actions_taken=0, actions_successful=0,
                )

            def get_rubric(self) -> str:
                return "Stale detection, adaptation quality"

            def get_schema_version(self, state: dict[str, Any]) -> int:
                return int(state.get("schema_version", 1))

            def get_mutations(self) -> list[SchemaMutation]:
                return [
                    SchemaMutation(
                        version=2, description="add priority",
                        fields_added=["priority"], fields_removed=[],
                        fields_modified={}, breaking=False,
                    ),
                ]

            def get_mutation_log(self, state: dict[str, Any]) -> list[SchemaMutation]:
                return [
                    SchemaMutation(
                        version=2, description="add priority",
                        fields_added=["priority"], fields_removed=[],
                        fields_modified={}, breaking=False,
                    ),
                ]

            def apply_mutation(self, state: dict[str, Any], mutation: SchemaMutation) -> dict[str, Any]:
                new_state = dict(state)
                new_state["schema_version"] = mutation.version
                new_state.setdefault("mutations", []).append(mutation.to_dict())
                return new_state

            def check_context_validity(
                self, state: dict[str, Any], assumptions: list[str]
            ) -> list[ContextValidity]:
                return [
                    ContextValidity(
                        assumption=a,
                        still_valid=state.get("schema_version", 1) < 2,
                        invalidated_by_version=2 if state.get("schema_version", 1) >= 2 else None,
                    )
                    for a in assumptions
                ]

            def evaluate_adaptation(self, state: dict[str, Any]) -> SchemaEvolutionResult:
                return SchemaEvolutionResult(
                    score=0.85, reasoning="Good", dimension_scores={"detection": 0.9},
                    mutations_applied=2, stale_assumptions_detected=2,
                    stale_assumptions_missed=0, recovery_actions_taken=2,
                    recovery_actions_successful=2,
                )

        return _M()


# ===========================================================================
# AC-254: Tool-fragility data models
# ===========================================================================


class TestToolContract:
    def test_construction(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolContract

        tc = ToolContract(
            tool_name="search_api",
            version=1,
            input_schema={"query": "str"},
            output_schema={"results": "list[str]"},
            description="Search endpoint",
        )
        assert tc.tool_name == "search_api"
        assert tc.version == 1

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolContract

        tc = ToolContract(
            tool_name="api",
            version=2,
            input_schema={"q": "str"},
            output_schema={"data": "list"},
            description="API v2",
        )
        data = tc.to_dict()
        restored = ToolContract.from_dict(data)
        assert restored.tool_name == tc.tool_name
        assert restored.version == tc.version
        assert restored.input_schema == tc.input_schema


class TestToolDrift:
    def test_construction(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolDrift

        td = ToolDrift(
            tool_name="search_api",
            from_version=1,
            to_version=2,
            description="Response format changed from list to paginated",
            drift_type="schema_change",
            breaking=True,
        )
        assert td.drift_type == "schema_change"
        assert td.breaking is True

    def test_non_breaking_drift(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolDrift

        td = ToolDrift(
            tool_name="cache",
            from_version=1,
            to_version=2,
            description="Added optional TTL parameter",
            drift_type="additive_change",
            breaking=False,
        )
        assert td.breaking is False

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolDrift

        td = ToolDrift(
            tool_name="api",
            from_version=1,
            to_version=2,
            description="changed",
            drift_type="schema_change",
            breaking=True,
        )
        data = td.to_dict()
        restored = ToolDrift.from_dict(data)
        assert restored.tool_name == td.tool_name
        assert restored.drift_type == td.drift_type
        assert restored.breaking is True


class TestFailureAttribution:
    def test_construction(self) -> None:
        from autocontext.scenarios.tool_fragility import FailureAttribution

        fa = FailureAttribution(
            step=3,
            failure_class="tool_failure",
            description="API returned unexpected schema",
            tool_name="search_api",
            recoverable=True,
        )
        assert fa.failure_class == "tool_failure"
        assert fa.recoverable is True

    def test_valid_failure_classes(self) -> None:
        from autocontext.scenarios.tool_fragility import FAILURE_CLASSES, FailureAttribution

        for fc in FAILURE_CLASSES:
            fa = FailureAttribution(
                step=1, failure_class=fc, description="test",
                tool_name="t", recoverable=True,
            )
            assert fa.failure_class == fc

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.tool_fragility import FailureAttribution

        fa = FailureAttribution(
            step=5, failure_class="routing_failure",
            description="Wrong tool selected", tool_name="api",
            recoverable=False,
        )
        data = fa.to_dict()
        restored = FailureAttribution.from_dict(data)
        assert restored.failure_class == fa.failure_class
        assert restored.recoverable is False


class TestToolFragilityResult:
    def test_construction(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolFragilityResult

        r = ToolFragilityResult(
            score=0.7,
            reasoning="Adapted to most drifts",
            dimension_scores={"adaptation": 0.8, "waste_avoidance": 0.6},
            drifts_injected=3,
            drifts_detected=2,
            drifts_adapted=2,
            wasted_attempts=1,
            failure_attributions=[],
        )
        assert r.score == 0.7
        assert r.drifts_detected == 2
        assert r.wasted_attempts == 1

    def test_to_dict_from_dict(self) -> None:
        from autocontext.scenarios.tool_fragility import FailureAttribution, ToolFragilityResult

        r = ToolFragilityResult(
            score=0.5,
            reasoning="Poor",
            dimension_scores={"adaptation": 0.3},
            drifts_injected=4,
            drifts_detected=1,
            drifts_adapted=1,
            wasted_attempts=3,
            failure_attributions=[
                FailureAttribution(
                    step=2, failure_class="tool_failure",
                    description="Schema mismatch", tool_name="api",
                    recoverable=True,
                ),
            ],
        )
        data = r.to_dict()
        restored = ToolFragilityResult.from_dict(data)
        assert restored.score == r.score
        assert restored.wasted_attempts == 3
        assert len(restored.failure_attributions) == 1
        assert restored.failure_attributions[0].failure_class == "tool_failure"


# ===========================================================================
# AC-254: ToolFragilityInterface ABC
# ===========================================================================


class TestToolFragilityInterfaceABC:
    def test_cannot_instantiate_abc(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolFragilityInterface

        with pytest.raises(TypeError, match="abstract"):
            ToolFragilityInterface()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        mock = self._make_mock()
        assert mock.name == "mock_tool_fragility"

    def test_describe_scenario(self) -> None:
        mock = self._make_mock()
        assert isinstance(mock.describe_scenario(), str)

    def test_get_tool_contracts(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolContract

        mock = self._make_mock()
        state = mock.initial_state()
        contracts = mock.get_tool_contracts(state)
        assert isinstance(contracts, list)
        assert len(contracts) >= 1
        assert all(isinstance(c, ToolContract) for c in contracts)

    def test_get_drift_log(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolDrift

        mock = self._make_mock()
        state = mock.initial_state()
        log = mock.get_drift_log(state)
        assert isinstance(log, list)
        assert all(isinstance(d, ToolDrift) for d in log)

    def test_inject_drift(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolDrift

        mock = self._make_mock()
        state = mock.initial_state()
        drift = ToolDrift(
            tool_name="search_api", from_version=1, to_version=2,
            description="Schema changed", drift_type="schema_change", breaking=True,
        )
        new_state = mock.inject_drift(state, drift)
        assert isinstance(new_state, dict)

    def test_attribute_failure(self) -> None:
        from autocontext.scenarios.tool_fragility import FailureAttribution

        mock = self._make_mock()
        state = mock.initial_state()
        attr = mock.attribute_failure(state, step=1, error="Schema mismatch")
        assert isinstance(attr, FailureAttribution)
        assert attr.failure_class in {
            "routing_failure", "stale_instruction_failure",
            "tool_failure", "stale_context_failure",
        }

    def test_evaluate_fragility(self) -> None:
        from autocontext.scenarios.tool_fragility import ToolFragilityResult

        mock = self._make_mock()
        state = mock.initial_state()
        result = mock.evaluate_fragility(state)
        assert isinstance(result, ToolFragilityResult)
        assert 0.0 <= result.score <= 1.0

    def test_initial_state(self) -> None:
        mock = self._make_mock()
        state = mock.initial_state(seed=42)
        assert isinstance(state, dict)

    def _make_mock(self) -> Any:
        from autocontext.scenarios.simulation import ActionResult, ActionSpec, EnvironmentSpec
        from autocontext.scenarios.tool_fragility import (
            FailureAttribution,
            ToolContract,
            ToolDrift,
            ToolFragilityInterface,
            ToolFragilityResult,
        )

        class _M(ToolFragilityInterface):
            name = "mock_tool_fragility"

            def describe_scenario(self) -> str:
                return "Tools drift while task stays the same"

            def describe_environment(self) -> EnvironmentSpec:
                return EnvironmentSpec(
                    name="mock_tool_fragility", description="drifting tools",
                    available_actions=[ActionSpec(name="call_api", description="call API", parameters={})],
                    initial_state_description="stable tools", success_criteria=["task completed"],
                )

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"tool_versions": {"search_api": 1}, "drifts": [], "seed": seed or 0}

            def get_available_actions(self, state: dict[str, Any]) -> list:
                return self.describe_environment().available_actions

            def execute_action(self, state: dict[str, Any], action: Any) -> tuple:
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
                return "Adaptation quality, waste avoidance"

            def get_tool_contracts(self, state: dict[str, Any]) -> list[ToolContract]:
                return [
                    ToolContract(
                        tool_name="search_api", version=1,
                        input_schema={"query": "str"},
                        output_schema={"results": "list"},
                        description="Search API",
                    ),
                ]

            def get_drift_log(self, state: dict[str, Any]) -> list[ToolDrift]:
                return [
                    ToolDrift(
                        tool_name="search_api", from_version=1, to_version=2,
                        description="Paginated response", drift_type="schema_change",
                        breaking=True,
                    ),
                ]

            def inject_drift(self, state: dict[str, Any], drift: ToolDrift) -> dict[str, Any]:
                new_state = dict(state)
                new_state.setdefault("drifts", []).append(drift.to_dict())
                tv = dict(new_state.get("tool_versions", {}))
                tv[drift.tool_name] = drift.to_version
                new_state["tool_versions"] = tv
                return new_state

            def attribute_failure(
                self, state: dict[str, Any], step: int, error: str
            ) -> FailureAttribution:
                return FailureAttribution(
                    step=step, failure_class="tool_failure",
                    description=error, tool_name="search_api",
                    recoverable=True,
                )

            def evaluate_fragility(self, state: dict[str, Any]) -> ToolFragilityResult:
                return ToolFragilityResult(
                    score=0.75, reasoning="Adapted to most drifts",
                    dimension_scores={"adaptation": 0.8},
                    drifts_injected=2, drifts_detected=2, drifts_adapted=1,
                    wasted_attempts=1, failure_attributions=[],
                )

        return _M()


# ===========================================================================
# Family registry integration
# ===========================================================================


class TestFamilyRegistration:
    def test_schema_evolution_family_registered(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("schema_evolution")
        assert family.name == "schema_evolution"
        assert family.evaluation_mode == "schema_adaptation"

    def test_schema_evolution_scenario_type_marker(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("schema_evolution")
        assert family.scenario_type_marker == "schema_evolution"

    def test_tool_fragility_family_registered(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("tool_fragility")
        assert family.name == "tool_fragility"
        assert family.evaluation_mode == "drift_adaptation"

    def test_tool_fragility_scenario_type_marker(self) -> None:
        from autocontext.scenarios.families import get_family

        family = get_family("tool_fragility")
        assert family.scenario_type_marker == "tool_fragility"

    def test_detect_family_schema_evolution(self) -> None:
        from autocontext.scenarios.families import detect_family

        mock = TestSchemaEvolutionInterfaceABC()._make_mock()
        family = detect_family(mock)
        assert family is not None
        assert family.name == "schema_evolution"

    def test_detect_family_tool_fragility(self) -> None:
        from autocontext.scenarios.families import detect_family

        mock = TestToolFragilityInterfaceABC()._make_mock()
        family = detect_family(mock)
        assert family is not None
        assert family.name == "tool_fragility"


# ===========================================================================
# Pipeline registry integration
# ===========================================================================


class TestSchemaEvolutionPipeline:
    def test_pipeline_registered(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import has_pipeline

        assert has_pipeline("schema_evolution") is True

    def test_pipeline_spec_validation_valid(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {
            "description": "Schema changes during API migration",
            "environment_description": "REST API backend",
            "initial_state_description": "v1 schema active",
            "mutations": [
                {"version": 2, "description": "add priority field", "breaking": False},
            ],
            "success_criteria": ["Agent adapts to all schema versions"],
            "actions": [{"name": "query_api"}],
        }
        errors = validate_for_family("schema_evolution", spec)
        assert errors == []

    def test_pipeline_spec_validation_missing_fields(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {"description": "Schema changes"}
        errors = validate_for_family("schema_evolution", spec)
        assert len(errors) > 0
        assert any("mutations" in e for e in errors)

    def test_pipeline_spec_empty_mutations(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {
            "description": "Schema changes",
            "environment_description": "backend",
            "initial_state_description": "v1",
            "mutations": [],
            "success_criteria": ["adapted"],
            "actions": [{"name": "query"}],
        }
        errors = validate_for_family("schema_evolution", spec)
        assert any("mutations" in e and "empty" in e for e in errors)

    def test_pipeline_source_validation(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface

class MyEvo(SchemaEvolutionInterface):
    name = "my_evo"
    def describe_scenario(self): return "scenario"
    def describe_environment(self): pass
    def initial_state(self, seed=None): return {}
    def get_available_actions(self, state): return []
    def execute_action(self, state, action): pass
    def is_terminal(self, state): return False
    def evaluate_trace(self, trace, final_state): pass
    def get_rubric(self): return "rubric"
    def get_mutations(self): return []
    def get_schema_version(self, state): return 1
    def get_mutation_log(self, state): return []
    def apply_mutation(self, state, mutation): return state
    def check_context_validity(self, state, assumptions): return []
    def evaluate_adaptation(self, state): pass
'''
        errors = validate_source_for_family("schema_evolution", source)
        assert errors == []

    def test_pipeline_source_missing_get_mutations(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface

class MyEvo(SchemaEvolutionInterface):
    name = "my_evo"
    def describe_scenario(self): return "scenario"
    def describe_environment(self): pass
    def initial_state(self, seed=None): return {}
    def get_available_actions(self, state): return []
    def execute_action(self, state, action): pass
    def is_terminal(self, state): return False
    def evaluate_trace(self, trace, final_state): pass
    def get_rubric(self): return "rubric"
    def get_schema_version(self, state): return 1
    def get_mutation_log(self, state): return []
    def apply_mutation(self, state, mutation): return state
    def check_context_validity(self, state, assumptions): return []
    def evaluate_adaptation(self, state): pass
'''
        errors = validate_source_for_family("schema_evolution", source)
        assert any("get_mutations" in e for e in errors)

    def test_pipeline_source_wrong_base_class(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
class NotASchemaEvo:
    pass
'''
        errors = validate_source_for_family("schema_evolution", source)
        assert any("SchemaEvolutionInterface" in e for e in errors)


class TestToolFragilityPipeline:
    def test_pipeline_registered(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import has_pipeline

        assert has_pipeline("tool_fragility") is True

    def test_pipeline_spec_validation_valid(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {
            "description": "API contracts drift during migration",
            "environment_description": "Microservice architecture",
            "initial_state_description": "All tools stable at v1",
            "tool_contracts": [
                {"tool_name": "search_api", "version": 1, "description": "Search endpoint"},
            ],
            "success_criteria": ["Agent completes task despite tool changes"],
            "actions": [{"name": "call_search"}],
        }
        errors = validate_for_family("tool_fragility", spec)
        assert errors == []

    def test_pipeline_spec_validation_missing_fields(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {"description": "Tools drift"}
        errors = validate_for_family("tool_fragility", spec)
        assert len(errors) > 0
        assert any("tool_contracts" in e for e in errors)

    def test_pipeline_spec_empty_tool_contracts(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        spec: dict[str, Any] = {
            "description": "Tools drift",
            "environment_description": "microservices",
            "initial_state_description": "stable",
            "tool_contracts": [],
            "success_criteria": ["adapted"],
            "actions": [{"name": "call"}],
        }
        errors = validate_for_family("tool_fragility", spec)
        assert any("tool_contracts" in e and "empty" in e for e in errors)

    def test_pipeline_source_validation(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
from autocontext.scenarios.tool_fragility import ToolFragilityInterface

class MyFrag(ToolFragilityInterface):
    name = "my_frag"
    def describe_scenario(self): return "scenario"
    def describe_environment(self): pass
    def initial_state(self, seed=None): return {}
    def get_available_actions(self, state): return []
    def execute_action(self, state, action): pass
    def is_terminal(self, state): return False
    def evaluate_trace(self, trace, final_state): pass
    def get_rubric(self): return "rubric"
    def get_tool_contracts(self, state): return []
    def get_drift_log(self, state): return []
    def inject_drift(self, state, drift): return state
    def attribute_failure(self, state, step, error): pass
    def evaluate_fragility(self, state): pass
'''
        errors = validate_source_for_family("tool_fragility", source)
        assert errors == []

    def test_pipeline_source_wrong_base_class(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family

        source = '''
class NotAToolFragility:
    pass
'''
        errors = validate_source_for_family("tool_fragility", source)
        assert any("ToolFragilityInterface" in e for e in errors)


# ===========================================================================
# Cross-family mismatch
# ===========================================================================


# ===========================================================================
# Classifier routing (hot path: classify)
# ===========================================================================


class TestClassifierRouting:
    def test_classify_schema_evolution_description(self) -> None:
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family

        result = classify_scenario_family(
            "Schema changes mid-run and the agent must detect stale context and adapt to the new version"
        )
        assert result.family_name == "schema_evolution"
        assert result.confidence > 0.3

    def test_classify_tool_fragility_description(self) -> None:
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family

        result = classify_scenario_family(
            "API contract drift where tools change their response schema while the core task stays the same"
        )
        assert result.family_name == "tool_fragility"
        assert result.confidence > 0.3

    def test_route_schema_evolution(self) -> None:
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family, route_to_family

        classification = classify_scenario_family(
            "Detect stale context after a schema evolution with breaking change and field removed"
        )
        family = route_to_family(classification)
        assert family.name == "schema_evolution"

    def test_route_tool_fragility(self) -> None:
        from autocontext.scenarios.custom.family_classifier import classify_scenario_family, route_to_family

        classification = classify_scenario_family(
            "Tool contract drift where the API response format changes between runs"
        )
        family = route_to_family(classification)
        assert family.name == "tool_fragility"


# ===========================================================================
# Designer/spec parsing (hot path: design)
# ===========================================================================


class TestSchemaEvolutionDesigner:
    def test_parse_spec(self) -> None:
        from autocontext.scenarios.custom.schema_evolution_designer import (
            SCHEMA_EVOLUTION_SPEC_END,
            SCHEMA_EVOLUTION_SPEC_START,
            parse_schema_evolution_spec,
        )

        raw = f"""{SCHEMA_EVOLUTION_SPEC_START}
{{
    "description": "API schema evolves",
    "environment_description": "REST backend",
    "initial_state_description": "v1 active",
    "mutations": [
        {{
            "version": 2, "description": "add field",
            "breaking": false, "fields_added": ["priority"],
            "fields_removed": [], "fields_modified": {{}}
        }}
    ],
    "success_criteria": ["adapted"],
    "failure_modes": ["stale cache"],
    "max_steps": 6,
    "actions": [
        {{
            "name": "query_api", "description": "query endpoint",
            "parameters": {{"endpoint": "string"}},
            "preconditions": [], "effects": ["data_fetched"]
        }}
    ]
}}
{SCHEMA_EVOLUTION_SPEC_END}"""
        spec = parse_schema_evolution_spec(raw)
        assert spec.description == "API schema evolves"
        assert len(spec.mutations) == 1
        assert spec.mutations[0].version == 2

    def test_design_fn_calls_llm(self) -> None:
        import json

        from autocontext.scenarios.custom.schema_evolution_designer import (
            SCHEMA_EVOLUTION_SPEC_END,
            SCHEMA_EVOLUTION_SPEC_START,
            design_schema_evolution,
        )

        fake_spec = {
            "description": "test",
            "environment_description": "env",
            "initial_state_description": "init",
            "mutations": [{
                "version": 2, "description": "add field", "breaking": False,
                "fields_added": ["x"], "fields_removed": [], "fields_modified": {},
            }],
            "success_criteria": ["ok"],
            "failure_modes": [],
            "max_steps": 5,
            "actions": [{"name": "query", "description": "q", "parameters": {}, "preconditions": [], "effects": []}],
        }

        def fake_llm(system: str, user: str) -> str:
            return f"{SCHEMA_EVOLUTION_SPEC_START}\n{json.dumps(fake_spec)}\n{SCHEMA_EVOLUTION_SPEC_END}"

        spec = design_schema_evolution("test description", fake_llm)
        assert spec.description == "test"


class TestToolFragilityDesigner:
    def test_parse_spec(self) -> None:
        from autocontext.scenarios.custom.tool_fragility_designer import (
            TOOL_FRAGILITY_SPEC_END,
            TOOL_FRAGILITY_SPEC_START,
            parse_tool_fragility_spec,
        )

        raw = f"""{TOOL_FRAGILITY_SPEC_START}
{{
    "description": "API contracts drift",
    "environment_description": "microservices",
    "initial_state_description": "stable",
    "tool_contracts": [
        {{"tool_name": "search_api", "version": 1, "description": "Search endpoint"}}
    ],
    "success_criteria": ["adapted"],
    "failure_modes": ["wrong tool selected"],
    "max_steps": 8,
    "actions": [
        {{"name": "call_search", "description": "call search API", "parameters": {{}}, "preconditions": [], "effects": []}}
    ]
}}
{TOOL_FRAGILITY_SPEC_END}"""
        spec = parse_tool_fragility_spec(raw)
        assert spec.description == "API contracts drift"
        assert len(spec.tool_contracts) == 1
        assert spec.tool_contracts[0].tool_name == "search_api"

    def test_design_fn_calls_llm(self) -> None:
        import json

        from autocontext.scenarios.custom.tool_fragility_designer import (
            TOOL_FRAGILITY_SPEC_END,
            TOOL_FRAGILITY_SPEC_START,
            design_tool_fragility,
        )

        fake_spec = {
            "description": "test",
            "environment_description": "env",
            "initial_state_description": "init",
            "tool_contracts": [{"tool_name": "api", "version": 1, "description": "d"}],
            "success_criteria": ["ok"],
            "failure_modes": [],
            "max_steps": 5,
            "actions": [{"name": "call", "description": "c", "parameters": {}, "preconditions": [], "effects": []}],
        }

        def fake_llm(system: str, user: str) -> str:
            return f"{TOOL_FRAGILITY_SPEC_START}\n{json.dumps(fake_spec)}\n{TOOL_FRAGILITY_SPEC_END}"

        spec = design_tool_fragility("test description", fake_llm)
        assert spec.description == "test"


# ===========================================================================
# Codegen (hot path: generate source)
# ===========================================================================


class TestSchemaEvolutionCodegen:
    def test_generate_class(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family
        from autocontext.scenarios.custom.schema_evolution_codegen import generate_schema_evolution_class
        from autocontext.scenarios.custom.schema_evolution_spec import SchemaEvolutionMutationModel, SchemaEvolutionSpec
        from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

        spec = SchemaEvolutionSpec(
            description="API schema evolves",
            environment_description="REST backend",
            initial_state_description="v1 active",
            mutations=[
                SchemaEvolutionMutationModel(
                    version=2, description="add priority", breaking=False,
                    fields_added=["priority"], fields_removed=[], fields_modified={},
                ),
            ],
            success_criteria=["adapted"],
            failure_modes=["stale cache"],
            actions=[SimulationActionSpecModel(name="query", description="query endpoint", parameters={"endpoint": "string"})],
            max_steps=6,
        )
        source = generate_schema_evolution_class(spec, "test_evo")
        errors = validate_source_for_family("schema_evolution", source)
        assert errors == [], f"Generated source has errors: {errors}"

    def test_generated_class_has_get_mutations(self) -> None:
        """AC-314: Generated schema evolution scenarios must have get_mutations()."""
        from autocontext.scenarios.custom.schema_evolution_codegen import generate_schema_evolution_class
        from autocontext.scenarios.custom.schema_evolution_spec import SchemaEvolutionMutationModel, SchemaEvolutionSpec
        from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

        spec = SchemaEvolutionSpec(
            description="Schema changes",
            environment_description="Backend",
            initial_state_description="v1",
            mutations=[
                SchemaEvolutionMutationModel(
                    version=2, description="add field", breaking=False,
                    fields_added=["priority"], fields_removed=[], fields_modified={},
                ),
                SchemaEvolutionMutationModel(
                    version=3, description="remove old field", breaking=True,
                    fields_added=[], fields_removed=["legacy_flag"], fields_modified={},
                ),
            ],
            success_criteria=["adapted"],
            failure_modes=["stale"],
            actions=[SimulationActionSpecModel(name="query", description="query", parameters={})],
            max_steps=5,
        )
        source = generate_schema_evolution_class(spec, "test_mutations")
        assert "def get_mutations" in source

    def test_get_mutations_returns_spec_mutations(self) -> None:
        """AC-314: get_mutations() should return the mutations from the spec."""
        import importlib.util
        import sys
        import tempfile
        from pathlib import Path

        from autocontext.scenarios.custom.schema_evolution_codegen import generate_schema_evolution_class
        from autocontext.scenarios.custom.schema_evolution_spec import SchemaEvolutionMutationModel, SchemaEvolutionSpec
        from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel
        from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface

        spec = SchemaEvolutionSpec(
            description="Evolving schema",
            environment_description="Backend",
            initial_state_description="v1",
            mutations=[
                SchemaEvolutionMutationModel(
                    version=2, description="add priority", breaking=False,
                    fields_added=["priority"], fields_removed=[], fields_modified={},
                ),
            ],
            success_criteria=["adapted"],
            failure_modes=["stale"],
            actions=[SimulationActionSpecModel(name="act", description="action", parameters={})],
            max_steps=5,
        )
        source = generate_schema_evolution_class(spec, "test_get_mutations")

        with tempfile.TemporaryDirectory() as tmp:
            mod_path = Path(tmp) / "test_mod.py"
            mod_path.write_text(source, encoding="utf-8")
            mod_name = f"_test_get_mutations_{id(source)}"
            mod_spec = importlib.util.spec_from_file_location(mod_name, str(mod_path))
            assert mod_spec is not None and mod_spec.loader is not None
            mod = importlib.util.module_from_spec(mod_spec)
            sys.modules[mod_name] = mod
            mod_spec.loader.exec_module(mod)

            cls = None
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and issubclass(attr, SchemaEvolutionInterface) and attr is not SchemaEvolutionInterface:
                    cls = attr
                    break

            assert cls is not None, "No SchemaEvolutionInterface subclass found"
            instance = cls()
            mutations = instance.get_mutations()
            assert len(mutations) == 1
            assert mutations[0].version == 2
            assert mutations[0].description == "add priority"

            sys.modules.pop(mod_name, None)

    def test_base_interface_requires_get_mutations(self) -> None:
        """AC-314: Base SchemaEvolutionInterface keeps get_mutations abstract."""
        from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface

        assert "get_mutations" in SchemaEvolutionInterface.__abstractmethods__


class TestToolFragilityCodegen:
    def test_generate_class(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_source_for_family
        from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel
        from autocontext.scenarios.custom.tool_fragility_codegen import generate_tool_fragility_class
        from autocontext.scenarios.custom.tool_fragility_spec import ToolContractSpecModel, ToolFragilitySpec

        spec = ToolFragilitySpec(
            description="API contracts drift",
            environment_description="microservices",
            initial_state_description="stable",
            tool_contracts=[
                ToolContractSpecModel(tool_name="search_api", version=1, description="Search endpoint"),
            ],
            success_criteria=["adapted"],
            failure_modes=["wrong tool"],
            actions=[SimulationActionSpecModel(name="call", description="call API", parameters={})],
            max_steps=8,
        )
        source = generate_tool_fragility_class(spec, "test_frag")
        errors = validate_source_for_family("tool_fragility", source)
        assert errors == [], f"Generated source has errors: {errors}"


# ===========================================================================
# Creator end-to-end (hot path: create → persist → load → register)
# ===========================================================================


class TestSchemaEvolutionCreator:
    def test_create_and_persist(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.custom.creator_registry import create_for_family
        from autocontext.scenarios.custom.schema_evolution_designer import (
            SCHEMA_EVOLUTION_SPEC_END,
            SCHEMA_EVOLUTION_SPEC_START,
        )
        from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface

        fake_spec = {
            "description": "test schema evo",
            "environment_description": "env",
            "initial_state_description": "v1",
            "mutations": [{
                "version": 2, "description": "add x", "breaking": False,
                "fields_added": ["x"], "fields_removed": [], "fields_modified": {},
            }],
            "success_criteria": ["adapted"],
            "failure_modes": [],
            "max_steps": 5,
            "actions": [{"name": "query", "description": "q", "parameters": {}, "preconditions": [], "effects": []}],
        }

        def fake_llm(system: str, user: str) -> str:
            return f"{SCHEMA_EVOLUTION_SPEC_START}\n{json.dumps(fake_spec)}\n{SCHEMA_EVOLUTION_SPEC_END}"

        creator = create_for_family("schema_evolution", fake_llm, tmp_path)
        scenario = creator.create("test schema evo", name="test_schema_evo_scenario")

        assert isinstance(scenario, SchemaEvolutionInterface)
        mutations = scenario.get_mutations()
        assert len(mutations) == 1
        assert mutations[0].version == 2
        scenario_dir = tmp_path / "_custom_scenarios" / "test_schema_evo_scenario"
        assert (scenario_dir / "scenario.py").exists()
        assert (scenario_dir / "spec.json").exists()
        assert (scenario_dir / "scenario_type.txt").exists()
        assert (scenario_dir / "scenario_type.txt").read_text().strip() == "schema_evolution"


class TestToolFragilityCreator:
    def test_create_and_persist(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.custom.tool_fragility_designer import (
            TOOL_FRAGILITY_SPEC_END,
            TOOL_FRAGILITY_SPEC_START,
        )
        from autocontext.scenarios.tool_fragility import ToolFragilityInterface

        fake_spec = {
            "description": "test tool frag",
            "environment_description": "env",
            "initial_state_description": "stable",
            "tool_contracts": [{"tool_name": "api", "version": 1, "description": "d"}],
            "success_criteria": ["adapted"],
            "failure_modes": [],
            "max_steps": 5,
            "actions": [{"name": "call", "description": "c", "parameters": {}, "preconditions": [], "effects": []}],
        }

        def fake_llm(system: str, user: str) -> str:
            return f"{TOOL_FRAGILITY_SPEC_START}\n{json.dumps(fake_spec)}\n{TOOL_FRAGILITY_SPEC_END}"

        from autocontext.scenarios.custom.creator_registry import create_for_family
        creator = create_for_family("tool_fragility", fake_llm, tmp_path)
        scenario = creator.create("test tool frag", name="test_tool_frag_scenario")

        assert isinstance(scenario, ToolFragilityInterface)
        scenario_dir = tmp_path / "_custom_scenarios" / "test_tool_frag_scenario"
        assert (scenario_dir / "scenario.py").exists()
        assert (scenario_dir / "spec.json").exists()
        assert (scenario_dir / "scenario_type.txt").exists()
        assert (scenario_dir / "scenario_type.txt").read_text().strip() == "tool_fragility"


# ===========================================================================
# Router dispatch from AgentTaskCreator (hot path: routing)
# ===========================================================================


class TestAgentTaskCreatorRouting:
    def test_routes_to_schema_evolution(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
        from autocontext.scenarios.custom.schema_evolution_designer import (
            SCHEMA_EVOLUTION_SPEC_END,
            SCHEMA_EVOLUTION_SPEC_START,
        )
        from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface

        fake_spec = {
            "description": "schema evo routing test",
            "environment_description": "env",
            "initial_state_description": "v1",
            "mutations": [{
                "version": 2, "description": "add x", "breaking": False,
                "fields_added": ["x"], "fields_removed": [], "fields_modified": {},
            }],
            "success_criteria": ["adapted"],
            "failure_modes": [],
            "max_steps": 5,
            "actions": [{"name": "query", "description": "q", "parameters": {}, "preconditions": [], "effects": []}],
        }

        def fake_llm(system: str, user: str) -> str:
            return f"{SCHEMA_EVOLUTION_SPEC_START}\n{json.dumps(fake_spec)}\n{SCHEMA_EVOLUTION_SPEC_END}"

        creator = AgentTaskCreator(fake_llm, tmp_path)
        scenario = creator.create(
            "Schema mutation scenario where the database schema changes mid-run and the agent must detect stale assumptions"
        )
        assert isinstance(scenario, SchemaEvolutionInterface)

    def test_routes_to_tool_fragility(self, tmp_path: Path) -> None:
        import json

        from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
        from autocontext.scenarios.custom.tool_fragility_designer import (
            TOOL_FRAGILITY_SPEC_END,
            TOOL_FRAGILITY_SPEC_START,
        )
        from autocontext.scenarios.tool_fragility import ToolFragilityInterface

        fake_spec = {
            "description": "tool frag routing test",
            "environment_description": "env",
            "initial_state_description": "stable",
            "tool_contracts": [{"tool_name": "api", "version": 1, "description": "d"}],
            "success_criteria": ["adapted"],
            "failure_modes": [],
            "max_steps": 5,
            "actions": [{"name": "call", "description": "c", "parameters": {}, "preconditions": [], "effects": []}],
        }

        def fake_llm(system: str, user: str) -> str:
            return f"{TOOL_FRAGILITY_SPEC_START}\n{json.dumps(fake_spec)}\n{TOOL_FRAGILITY_SPEC_END}"

        creator = AgentTaskCreator(fake_llm, tmp_path)
        scenario = creator.create(
            "Tool contract drift scenario where the API response schema changes while the core task remains the same"
        )
        assert isinstance(scenario, ToolFragilityInterface)


# ===========================================================================
# Cross-family mismatch
# ===========================================================================


class TestCrossFamilyMismatch:
    def test_schema_evo_spec_through_tool_fragility_pipeline(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        evo_spec: dict[str, Any] = {
            "description": "Schema changes",
            "environment_description": "backend",
            "initial_state_description": "v1",
            "mutations": [{"version": 2, "description": "change", "breaking": False}],
            "success_criteria": ["adapted"],
            "actions": [{"name": "query"}],
        }
        errors = validate_for_family("tool_fragility", evo_spec)
        assert len(errors) > 0, "Schema-evo spec should fail tool-fragility validation"

    def test_tool_fragility_spec_through_schema_evo_pipeline(self) -> None:
        from autocontext.scenarios.custom.family_pipeline import validate_for_family

        frag_spec: dict[str, Any] = {
            "description": "Tools drift",
            "environment_description": "microservices",
            "initial_state_description": "stable",
            "tool_contracts": [{"tool_name": "api", "version": 1, "description": "d"}],
            "success_criteria": ["adapted"],
            "actions": [{"name": "call"}],
        }
        errors = validate_for_family("schema_evolution", frag_spec)
        assert len(errors) > 0, "Tool-fragility spec should fail schema-evo validation"
