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


# ---------------------------------------------------------------------------
# Built-in pipeline registration
# ---------------------------------------------------------------------------

def _register_builtins() -> None:
    register_pipeline(AgentTaskPipeline())
    register_pipeline(SimulationPipeline())
    register_pipeline(ArtifactEditingPipeline())


_register_builtins()
