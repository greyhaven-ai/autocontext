"""Family-specific generator and validator pipelines (AC-247).

Defines per-family pipeline interfaces for spec validation, source
validation, and contract checking. Pipelines are registered explicitly;
unsupported families raise a structured error instead of silently
collapsing into a generic path.
"""

from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from typing import Any

# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class FamilyPipeline(ABC):
    """Base class for family-specific generator and validator pipelines."""

    @property
    @abstractmethod
    def family_name(self) -> str:
        """Return the scenario family this pipeline serves."""

    @abstractmethod
    def required_spec_fields(self) -> set[str]:
        """Return the set of required fields in a spec dict for this family."""

    @abstractmethod
    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        """Validate a spec dict. Returns a list of error strings (empty = valid)."""

    @abstractmethod
    def validate_source(self, source: str) -> list[str]:
        """Validate generated source code. Returns a list of error strings."""

    @abstractmethod
    def validate_contract(self, source: str) -> list[str]:
        """Validate that source implements the family's interface contract."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnsupportedFamilyError(Exception):
    """Raised when no pipeline exists for a requested family.

    Carries structured metadata for the caller to present alternatives
    instead of silently collapsing into a generic path.
    """

    def __init__(self, family_name: str, available_pipelines: list[str] | None = None) -> None:
        self.family_name = family_name
        self.available_pipelines = available_pipelines or list(PIPELINE_REGISTRY.keys())
        super().__init__(
            f"No pipeline registered for family '{family_name}'. "
            f"Available: {self.available_pipelines}"
        )


class FamilyContractError(Exception):
    """Raised when generated source violates the family's interface contract."""

    def __init__(self, family_name: str, errors: list[str]) -> None:
        self.family_name = family_name
        self.errors = errors
        super().__init__(
            f"Contract violations for family '{family_name}': {'; '.join(errors)}"
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PIPELINE_REGISTRY: dict[str, FamilyPipeline] = {}


def register_pipeline(pipeline: FamilyPipeline) -> None:
    """Register a family pipeline. Raises ValueError on duplicate."""
    if pipeline.family_name in PIPELINE_REGISTRY:
        raise ValueError(f"Pipeline for family '{pipeline.family_name}' is already registered")
    PIPELINE_REGISTRY[pipeline.family_name] = pipeline


def get_pipeline(family_name: str) -> FamilyPipeline:
    """Get a pipeline by family name. Raises UnsupportedFamilyError if missing."""
    if family_name not in PIPELINE_REGISTRY:
        raise UnsupportedFamilyError(family_name)
    return PIPELINE_REGISTRY[family_name]


def has_pipeline(family_name: str) -> bool:
    """Check whether a pipeline is registered for the given family."""
    return family_name in PIPELINE_REGISTRY


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def validate_for_family(family_name: str, spec: dict[str, Any]) -> list[str]:
    """Route spec validation to the family-specific pipeline."""
    pipeline = get_pipeline(family_name)
    return pipeline.validate_spec(spec)


def validate_source_for_family(family_name: str, source: str) -> list[str]:
    """Route source validation to the family-specific pipeline."""
    pipeline = get_pipeline(family_name)
    errors = pipeline.validate_source(source)
    if not errors:
        errors.extend(pipeline.validate_contract(source))
    return errors


# ---------------------------------------------------------------------------
# Concrete pipelines
# ---------------------------------------------------------------------------

_VALID_OUTPUT_FORMATS = {"free_text", "json_schema", "code"}


def _check_required_fields(spec: dict[str, Any], required: set[str]) -> list[str]:
    """Check that all required fields are present and non-empty."""
    errors: list[str] = []
    for field in sorted(required):
        if field not in spec:
            errors.append(f"missing required field: {field}")
        elif isinstance(spec[field], str) and not spec[field].strip():
            errors.append(f"field '{field}' must not be empty")
    return errors


def _check_source_for_class(source: str, base_class_name: str) -> list[str]:
    """Check that source code contains a subclass of the given base class."""
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error at line {exc.lineno}: {exc.msg}"]

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == base_class_name:
                    found = True
                    break
        if found:
            break

    if not found:
        errors.append(f"no {base_class_name} subclass found in generated code")
    return errors


def _check_required_methods(
    source: str,
    base_class_name: str,
    required_methods: set[str],
) -> list[str]:
    """Check that a subclass of the base class defines all required methods."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error at line {exc.lineno}: {exc.msg}"]

    subclasses: list[ast.ClassDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name == base_class_name:
                subclasses.append(node)
                break

    if not subclasses:
        return []

    for subclass in subclasses:
        implemented = {
            node.name
            for node in subclass.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        missing = sorted(required_methods - implemented)
        if not missing:
            return []

    return [
        f"generated {base_class_name} subclass is missing required methods: {', '.join(missing)}"
    ]


class AgentTaskPipeline(FamilyPipeline):
    """Pipeline for agent_task family scenarios."""

    @property
    def family_name(self) -> str:
        return "agent_task"

    def required_spec_fields(self) -> set[str]:
        return {"task_prompt", "judge_rubric"}

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        errors = _check_required_fields(spec, self.required_spec_fields())
        if errors:
            return errors

        try:
            spec_obj = AgentTaskSpec(**spec)
        except TypeError as exc:
            return [f"invalid agent_task spec: {exc}"]
        return validate_spec(spec_obj)

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "AgentTaskInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "AgentTaskInterface",
            {
                "get_task_prompt",
                "evaluate_output",
                "get_rubric",
                "initial_state",
                "describe_task",
            },
        )


class SimulationPipeline(FamilyPipeline):
    """Pipeline for simulation family scenarios."""

    @property
    def family_name(self) -> str:
        return "simulation"

    def required_spec_fields(self) -> set[str]:
        return {
            "description",
            "environment_description",
            "initial_state_description",
            "success_criteria",
            "actions",
        }

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        errors = _check_required_fields(spec, self.required_spec_fields())

        actions = spec.get("actions")
        if isinstance(actions, list):
            if len(actions) == 0:
                errors.append("actions must not be empty")
            else:
                for i, action in enumerate(actions):
                    if not isinstance(action, dict):
                        errors.append(f"actions[{i}] must be a dict")
                    elif "name" not in action:
                        errors.append(f"actions[{i}] missing 'name'")

        criteria = spec.get("success_criteria")
        if isinstance(criteria, list) and len(criteria) == 0:
            errors.append("success_criteria must not be empty")

        max_steps = spec.get("max_steps")
        if max_steps is not None and (not isinstance(max_steps, int) or max_steps <= 0):
            errors.append("max_steps must be a positive integer")

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "SimulationInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "SimulationInterface",
            {
                "describe_scenario",
                "describe_environment",
                "initial_state",
                "get_available_actions",
                "execute_action",
                "is_terminal",
                "evaluate_trace",
                "get_rubric",
            },
        )


class ArtifactEditingPipeline(FamilyPipeline):
    """Pipeline for artifact_editing family scenarios."""

    @property
    def family_name(self) -> str:
        return "artifact_editing"

    def required_spec_fields(self) -> set[str]:
        return {"task_description", "artifacts", "validation_rules", "rubric"}

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        from autocontext.scenarios.custom.artifact_editing_spec import (
            ArtifactEditingSpec,
            ArtifactSpecModel,
        )

        errors = _check_required_fields(spec, self.required_spec_fields())
        if errors:
            return errors

        artifacts = spec.get("artifacts")
        if isinstance(artifacts, list):
            if len(artifacts) == 0:
                errors.append("artifacts must not be empty")
            else:
                for i, artifact in enumerate(artifacts):
                    if not isinstance(artifact, dict):
                        errors.append(f"artifacts[{i}] must be a dict")
                    else:
                        if "path" not in artifact:
                            errors.append(f"artifacts[{i}] missing 'path'")
                        if "content" not in artifact:
                            errors.append(f"artifacts[{i}] missing 'content'")
                        if "content_type" not in artifact:
                            errors.append(f"artifacts[{i}] missing 'content_type'")

        rules = spec.get("validation_rules")
        if isinstance(rules, list) and len(rules) == 0:
            errors.append("validation_rules must not be empty")

        if errors:
            return errors

        try:
            ArtifactEditingSpec(
                task_description=str(spec["task_description"]),
                rubric=str(spec["rubric"]),
                validation_rules=[str(rule) for rule in spec["validation_rules"]],
                artifacts=[
                    ArtifactSpecModel(
                        path=str(artifact["path"]),
                        content=str(artifact["content"]),
                        content_type=str(artifact["content_type"]),
                        metadata=artifact.get("metadata", {}) if isinstance(artifact, dict) else {},
                    )
                    for artifact in spec["artifacts"]
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            return [f"invalid artifact_editing spec: {exc}"]

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "ArtifactEditingInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "ArtifactEditingInterface",
            {
                "describe_task",
                "get_rubric",
                "initial_artifacts",
                "get_edit_prompt",
                "validate_artifact",
                "evaluate_edits",
            },
        )


class InvestigationPipeline(FamilyPipeline):
    """Pipeline for investigation family scenarios."""

    @property
    def family_name(self) -> str:
        return "investigation"

    def required_spec_fields(self) -> set[str]:
        return {
            "description",
            "environment_description",
            "initial_state_description",
            "evidence_pool_description",
            "diagnosis_target",
            "success_criteria",
            "actions",
        }

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        errors = _check_required_fields(spec, self.required_spec_fields())

        actions = spec.get("actions")
        if isinstance(actions, list):
            if len(actions) == 0:
                errors.append("actions must not be empty")
            else:
                for i, action in enumerate(actions):
                    if not isinstance(action, dict):
                        errors.append(f"actions[{i}] must be a dict")
                    elif "name" not in action:
                        errors.append(f"actions[{i}] missing 'name'")

        criteria = spec.get("success_criteria")
        if isinstance(criteria, list) and len(criteria) == 0:
            errors.append("success_criteria must not be empty")

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "InvestigationInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "InvestigationInterface",
            {
                "describe_scenario",
                "describe_environment",
                "initial_state",
                "get_available_actions",
                "execute_action",
                "is_terminal",
                "evaluate_trace",
                "get_rubric",
                "get_evidence_pool",
                "evaluate_evidence_chain",
                "evaluate_diagnosis",
            },
        )


class WorkflowPipeline(FamilyPipeline):
    """Pipeline for workflow family scenarios."""

    @property
    def family_name(self) -> str:
        return "workflow"

    def required_spec_fields(self) -> set[str]:
        return {
            "description",
            "environment_description",
            "initial_state_description",
            "workflow_steps",
            "success_criteria",
            "actions",
        }

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        errors = _check_required_fields(spec, self.required_spec_fields())

        workflow_steps = spec.get("workflow_steps")
        if isinstance(workflow_steps, list):
            if len(workflow_steps) == 0:
                errors.append("workflow_steps must not be empty")
            else:
                for i, step in enumerate(workflow_steps):
                    if not isinstance(step, dict):
                        errors.append(f"workflow_steps[{i}] must be a dict")
                    elif "name" not in step:
                        errors.append(f"workflow_steps[{i}] missing 'name'")

        actions = spec.get("actions")
        if isinstance(actions, list):
            if len(actions) == 0:
                errors.append("actions must not be empty")
            else:
                for i, action in enumerate(actions):
                    if not isinstance(action, dict):
                        errors.append(f"actions[{i}] must be a dict")
                    elif "name" not in action:
                        errors.append(f"actions[{i}] missing 'name'")

        criteria = spec.get("success_criteria")
        if isinstance(criteria, list) and len(criteria) == 0:
            errors.append("success_criteria must not be empty")

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "WorkflowInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "WorkflowInterface",
            {
                "describe_scenario",
                "describe_environment",
                "initial_state",
                "get_available_actions",
                "execute_action",
                "is_terminal",
                "evaluate_trace",
                "get_rubric",
                "get_workflow_steps",
                "execute_step",
                "execute_compensation",
                "get_side_effects",
                "evaluate_workflow",
            },
        )


class SchemaEvolutionPipeline(FamilyPipeline):
    """Pipeline for schema_evolution family scenarios."""

    @property
    def family_name(self) -> str:
        return "schema_evolution"

    def required_spec_fields(self) -> set[str]:
        return {
            "description",
            "environment_description",
            "initial_state_description",
            "mutations",
            "success_criteria",
            "actions",
        }

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        errors = _check_required_fields(spec, self.required_spec_fields())

        mutations = spec.get("mutations")
        if isinstance(mutations, list):
            if len(mutations) == 0:
                errors.append("mutations must not be empty")
            else:
                for i, mutation in enumerate(mutations):
                    if not isinstance(mutation, dict):
                        errors.append(f"mutations[{i}] must be a dict")
                    elif "version" not in mutation:
                        errors.append(f"mutations[{i}] missing 'version'")

        actions = spec.get("actions")
        if isinstance(actions, list):
            if len(actions) == 0:
                errors.append("actions must not be empty")
            else:
                for i, action in enumerate(actions):
                    if not isinstance(action, dict):
                        errors.append(f"actions[{i}] must be a dict")
                    elif "name" not in action:
                        errors.append(f"actions[{i}] missing 'name'")

        criteria = spec.get("success_criteria")
        if isinstance(criteria, list) and len(criteria) == 0:
            errors.append("success_criteria must not be empty")

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "SchemaEvolutionInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "SchemaEvolutionInterface",
            {
                "describe_scenario",
                "describe_environment",
                "initial_state",
                "get_available_actions",
                "execute_action",
                "is_terminal",
                "evaluate_trace",
                "get_rubric",
                "get_mutations",
                "get_schema_version",
                "get_mutation_log",
                "apply_mutation",
                "check_context_validity",
                "evaluate_adaptation",
            },
        )


class ToolFragilityPipeline(FamilyPipeline):
    """Pipeline for tool_fragility family scenarios."""

    @property
    def family_name(self) -> str:
        return "tool_fragility"

    def required_spec_fields(self) -> set[str]:
        return {
            "description",
            "environment_description",
            "initial_state_description",
            "tool_contracts",
            "success_criteria",
            "actions",
        }

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        errors = _check_required_fields(spec, self.required_spec_fields())

        tool_contracts = spec.get("tool_contracts")
        if isinstance(tool_contracts, list):
            if len(tool_contracts) == 0:
                errors.append("tool_contracts must not be empty")
            else:
                for i, tc in enumerate(tool_contracts):
                    if not isinstance(tc, dict):
                        errors.append(f"tool_contracts[{i}] must be a dict")
                    elif "tool_name" not in tc:
                        errors.append(f"tool_contracts[{i}] missing 'tool_name'")

        actions = spec.get("actions")
        if isinstance(actions, list):
            if len(actions) == 0:
                errors.append("actions must not be empty")
            else:
                for i, action in enumerate(actions):
                    if not isinstance(action, dict):
                        errors.append(f"actions[{i}] must be a dict")
                    elif "name" not in action:
                        errors.append(f"actions[{i}] missing 'name'")

        criteria = spec.get("success_criteria")
        if isinstance(criteria, list) and len(criteria) == 0:
            errors.append("success_criteria must not be empty")

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "ToolFragilityInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "ToolFragilityInterface",
            {
                "describe_scenario",
                "describe_environment",
                "initial_state",
                "get_available_actions",
                "execute_action",
                "is_terminal",
                "evaluate_trace",
                "get_rubric",
                "get_tool_contracts",
                "get_drift_log",
                "inject_drift",
                "attribute_failure",
                "evaluate_fragility",
            },
        )


class NegotiationPipeline(FamilyPipeline):
    """Pipeline for negotiation family scenarios."""

    @property
    def family_name(self) -> str:
        return "negotiation"

    def required_spec_fields(self) -> set[str]:
        return {
            "description",
            "environment_description",
            "initial_state_description",
            "hidden_preferences",
            "max_rounds",
            "success_criteria",
            "actions",
        }

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        errors = _check_required_fields(spec, self.required_spec_fields())

        hp = spec.get("hidden_preferences")
        if isinstance(hp, dict):
            for key in ("priorities", "reservation_value", "aspiration_value", "batna_description"):
                if key not in hp:
                    errors.append(f"hidden_preferences missing '{key}'")
        elif hp is not None:
            errors.append("hidden_preferences must be a dict")

        actions = spec.get("actions")
        if isinstance(actions, list):
            if len(actions) == 0:
                errors.append("actions must not be empty")
            else:
                for i, action in enumerate(actions):
                    if not isinstance(action, dict):
                        errors.append(f"actions[{i}] must be a dict")
                    elif "name" not in action:
                        errors.append(f"actions[{i}] missing 'name'")

        criteria = spec.get("success_criteria")
        if isinstance(criteria, list) and len(criteria) == 0:
            errors.append("success_criteria must not be empty")

        max_rounds = spec.get("max_rounds")
        if max_rounds is not None and (not isinstance(max_rounds, int) or max_rounds <= 0):
            errors.append("max_rounds must be a positive integer")

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "NegotiationInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "NegotiationInterface",
            {
                "describe_scenario",
                "describe_environment",
                "initial_state",
                "get_available_actions",
                "execute_action",
                "is_terminal",
                "evaluate_trace",
                "get_rubric",
                "get_hidden_preferences",
                "get_rounds",
                "get_opponent_model",
                "update_opponent_model",
                "evaluate_negotiation",
            },
        )


class OperatorLoopPipeline(FamilyPipeline):
    """Pipeline for operator_loop family scenarios."""

    @property
    def family_name(self) -> str:
        return "operator_loop"

    def required_spec_fields(self) -> set[str]:
        return {
            "description",
            "environment_description",
            "initial_state_description",
            "escalation_policy",
            "success_criteria",
            "actions",
        }

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        errors = _check_required_fields(spec, self.required_spec_fields())

        ep = spec.get("escalation_policy")
        if isinstance(ep, dict):
            for key in ("escalation_threshold", "max_escalations"):
                if key not in ep:
                    errors.append(f"escalation_policy missing '{key}'")
        elif ep is not None:
            errors.append("escalation_policy must be a dict")

        actions = spec.get("actions")
        if isinstance(actions, list):
            if len(actions) == 0:
                errors.append("actions must not be empty")
            else:
                for i, action in enumerate(actions):
                    if not isinstance(action, dict):
                        errors.append(f"actions[{i}] must be a dict")
                    elif "name" not in action:
                        errors.append(f"actions[{i}] missing 'name'")

        criteria = spec.get("success_criteria")
        if isinstance(criteria, list) and len(criteria) == 0:
            errors.append("success_criteria must not be empty")

        max_steps = spec.get("max_steps")
        if max_steps is not None and (not isinstance(max_steps, int) or max_steps <= 0):
            errors.append("max_steps must be a positive integer")

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "OperatorLoopInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "OperatorLoopInterface",
            {
                "describe_scenario",
                "describe_environment",
                "initial_state",
                "get_available_actions",
                "execute_action",
                "is_terminal",
                "evaluate_trace",
                "get_rubric",
                "get_escalation_log",
                "get_clarification_log",
                "escalate",
                "request_clarification",
                "evaluate_judgment",
            },
        )


class CoordinationPipeline(FamilyPipeline):
    """Pipeline for coordination family scenarios."""

    @property
    def family_name(self) -> str:
        return "coordination"

    def required_spec_fields(self) -> set[str]:
        return {
            "description",
            "environment_description",
            "initial_state_description",
            "workers",
            "success_criteria",
            "actions",
        }

    def validate_spec(self, spec: dict[str, Any]) -> list[str]:
        errors = _check_required_fields(spec, self.required_spec_fields())

        workers = spec.get("workers")
        if isinstance(workers, list):
            if len(workers) == 0:
                errors.append("workers must not be empty")
            else:
                for i, worker in enumerate(workers):
                    if not isinstance(worker, dict):
                        errors.append(f"workers[{i}] must be a dict")
                    elif "worker_id" not in worker:
                        errors.append(f"workers[{i}] missing 'worker_id'")
        elif workers is not None:
            errors.append("workers must be a list")

        actions = spec.get("actions")
        if isinstance(actions, list):
            if len(actions) == 0:
                errors.append("actions must not be empty")
            else:
                for i, action in enumerate(actions):
                    if not isinstance(action, dict):
                        errors.append(f"actions[{i}] must be a dict")
                    elif "name" not in action:
                        errors.append(f"actions[{i}] missing 'name'")

        criteria = spec.get("success_criteria")
        if isinstance(criteria, list) and len(criteria) == 0:
            errors.append("success_criteria must not be empty")

        max_steps = spec.get("max_steps")
        if max_steps is not None and (not isinstance(max_steps, int) or max_steps <= 0):
            errors.append("max_steps must be a positive integer")

        return errors

    def validate_source(self, source: str) -> list[str]:
        return _check_source_for_class(source, "CoordinationInterface")

    def validate_contract(self, source: str) -> list[str]:
        return _check_required_methods(
            source,
            "CoordinationInterface",
            {
                "describe_scenario",
                "describe_environment",
                "initial_state",
                "get_available_actions",
                "execute_action",
                "is_terminal",
                "evaluate_trace",
                "get_rubric",
                "get_worker_contexts",
                "get_handoff_log",
                "record_handoff",
                "merge_outputs",
                "evaluate_coordination",
            },
        )


# ---------------------------------------------------------------------------
# Built-in pipeline registration
# ---------------------------------------------------------------------------

def _register_builtins() -> None:
    register_pipeline(AgentTaskPipeline())
    register_pipeline(SimulationPipeline())
    register_pipeline(ArtifactEditingPipeline())
    register_pipeline(InvestigationPipeline())
    register_pipeline(WorkflowPipeline())
    register_pipeline(SchemaEvolutionPipeline())
    register_pipeline(ToolFragilityPipeline())
    register_pipeline(NegotiationPipeline())
    register_pipeline(OperatorLoopPipeline())
    register_pipeline(CoordinationPipeline())


_register_builtins()
