"""Tests for the generic scenario creator and legacy compatibility shims."""

from __future__ import annotations

import importlib
from pathlib import Path


class TestGenericCreatorExists:
    """The generic creator module should exist and be importable."""

    def test_generic_creator_importable(self) -> None:
        from autocontext.scenarios.custom.generic_creator import GenericScenarioCreator

        assert GenericScenarioCreator is not None

    def test_generic_creator_has_create_method(self) -> None:
        from autocontext.scenarios.custom.generic_creator import GenericScenarioCreator

        assert hasattr(GenericScenarioCreator, "create")


class TestPerFamilyCreatorCompatibility:
    """Legacy per-family creator modules should remain importable."""

    COMPAT_CREATORS = [
        ("artifact_editing_creator", "ArtifactEditingCreator"),
        ("coordination_creator", "CoordinationCreator"),
        ("investigation_creator", "InvestigationCreator"),
        ("negotiation_creator", "NegotiationCreator"),
        ("operator_loop_creator", "OperatorLoopCreator"),
        ("schema_evolution_creator", "SchemaEvolutionCreator"),
        ("simulation_creator", "SimulationCreator"),
        ("tool_fragility_creator", "ToolFragilityCreator"),
        ("workflow_creator", "WorkflowCreator"),
    ]

    def test_creator_modules_remain_importable(self) -> None:
        for module_name, class_name in self.COMPAT_CREATORS:
            module = importlib.import_module(
                f"autocontext.scenarios.custom.{module_name}"
            )
            assert getattr(module, class_name) is not None

    def test_creator_classes_keep_constructor_shape(self, tmp_path: Path) -> None:
        def fake_llm(system: str, user: str) -> str:
            return ""

        for module_name, class_name in self.COMPAT_CREATORS:
            module = importlib.import_module(
                f"autocontext.scenarios.custom.{module_name}"
            )
            creator_cls = getattr(module, class_name)
            creator = creator_cls(fake_llm, tmp_path)
            assert hasattr(creator, "create")

    def test_compatibility_helpers_remain_available(self) -> None:
        from autocontext.scenarios.custom.operator_loop_creator import (
            validate_operator_loop_spec,
        )
        from autocontext.scenarios.custom.simulation_creator import (
            should_use_simulation_family,
            validate_simulation_spec,
        )

        assert callable(should_use_simulation_family)
        assert callable(validate_simulation_spec)
        assert callable(validate_operator_loop_spec)
