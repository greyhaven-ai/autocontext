"""Family creator registry — maps family names to GenericScenarioCreator configs (AC-471)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.generic_creator import GenericScenarioCreator


@dataclass(frozen=True)
class FamilyCreatorConfig:
    """Configuration for creating scenarios of a specific family."""

    family: str
    designer_fn_path: str  # module:function for lazy import
    codegen_fn_path: str  # module:function for lazy import
    interface_class_path: str  # module:class for lazy import


def _lazy_import(dotted_path: str) -> Any:
    """Import a name from a dotted path like 'module.submodule:name'."""
    module_path, _, attr_name = dotted_path.rpartition(":")
    if not module_path:
        module_path, _, attr_name = dotted_path.rpartition(".")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


FAMILY_CONFIGS: dict[str, FamilyCreatorConfig] = {
    "simulation": FamilyCreatorConfig(
        family="simulation",
        designer_fn_path="autocontext.scenarios.custom.simulation_designer:design_simulation",
        codegen_fn_path="autocontext.scenarios.custom.simulation_codegen:generate_simulation_class",
        interface_class_path="autocontext.scenarios.simulation:SimulationInterface",
    ),
    "artifact_editing": FamilyCreatorConfig(
        family="artifact_editing",
        designer_fn_path="autocontext.scenarios.custom.artifact_editing_designer:design_artifact_editing",
        codegen_fn_path="autocontext.scenarios.custom.artifact_editing_codegen:generate_artifact_editing_class",
        interface_class_path="autocontext.scenarios.artifact_editing:ArtifactEditingInterface",
    ),
    "investigation": FamilyCreatorConfig(
        family="investigation",
        designer_fn_path="autocontext.scenarios.custom.investigation_designer:design_investigation",
        codegen_fn_path="autocontext.scenarios.custom.investigation_codegen:generate_investigation_class",
        interface_class_path="autocontext.scenarios.investigation:InvestigationInterface",
    ),
    "workflow": FamilyCreatorConfig(
        family="workflow",
        designer_fn_path="autocontext.scenarios.custom.workflow_designer:design_workflow",
        codegen_fn_path="autocontext.scenarios.custom.workflow_codegen:generate_workflow_class",
        interface_class_path="autocontext.scenarios.workflow:WorkflowInterface",
    ),
    "schema_evolution": FamilyCreatorConfig(
        family="schema_evolution",
        designer_fn_path="autocontext.scenarios.custom.schema_evolution_designer:design_schema_evolution",
        codegen_fn_path="autocontext.scenarios.custom.schema_evolution_codegen:generate_schema_evolution_class",
        interface_class_path="autocontext.scenarios.schema_evolution:SchemaEvolutionInterface",
    ),
    "tool_fragility": FamilyCreatorConfig(
        family="tool_fragility",
        designer_fn_path="autocontext.scenarios.custom.tool_fragility_designer:design_tool_fragility",
        codegen_fn_path="autocontext.scenarios.custom.tool_fragility_codegen:generate_tool_fragility_class",
        interface_class_path="autocontext.scenarios.tool_fragility:ToolFragilityInterface",
    ),
    "negotiation": FamilyCreatorConfig(
        family="negotiation",
        designer_fn_path="autocontext.scenarios.custom.negotiation_designer:design_negotiation",
        codegen_fn_path="autocontext.scenarios.custom.negotiation_codegen:generate_negotiation_class",
        interface_class_path="autocontext.scenarios.negotiation:NegotiationInterface",
    ),
    "operator_loop": FamilyCreatorConfig(
        family="operator_loop",
        designer_fn_path="autocontext.scenarios.custom.operator_loop_designer:design_operator_loop",
        codegen_fn_path="autocontext.scenarios.custom.operator_loop_codegen:generate_operator_loop_class",
        interface_class_path="autocontext.scenarios.operator_loop:OperatorLoopInterface",
    ),
    "coordination": FamilyCreatorConfig(
        family="coordination",
        designer_fn_path="autocontext.scenarios.custom.coordination_designer:design_coordination",
        codegen_fn_path="autocontext.scenarios.custom.coordination_codegen:generate_coordination_class",
        interface_class_path="autocontext.scenarios.coordination:CoordinationInterface",
    ),
}


def create_for_family(
    family: str,
    llm_fn: LlmFn,
    knowledge_root: Path,
) -> GenericScenarioCreator:
    """Create a GenericScenarioCreator configured for the given family."""
    config = FAMILY_CONFIGS.get(family)
    if config is None:
        msg = f"Unknown family: {family}. Known: {sorted(FAMILY_CONFIGS)}"
        raise ValueError(msg)

    return GenericScenarioCreator(
        family=config.family,
        designer_fn=_lazy_import(config.designer_fn_path),
        codegen_fn=_lazy_import(config.codegen_fn_path),
        interface_class=_lazy_import(config.interface_class_path),
        llm_fn=llm_fn,
        knowledge_root=knowledge_root,
    )
