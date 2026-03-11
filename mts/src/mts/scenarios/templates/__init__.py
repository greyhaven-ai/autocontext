"""Scenario template library for ready-to-use agent task scenarios.

Provides pre-built templates that can be scaffolded into new scenarios
and registered in the SCENARIO_REGISTRY.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from mts.scenarios.custom.agent_task_spec import AgentTaskSpec

TEMPLATE_DIR = Path(__file__).parent


@dataclass(slots=True)
class RubricDimension:
    """A single scoring dimension with a weight."""

    name: str
    description: str
    weight: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RubricDimension:
        """Create a RubricDimension from a dictionary."""
        return cls(
            name=data["name"],
            description=data["description"],
            weight=data.get("weight", 1.0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        return {"name": self.name, "description": self.description, "weight": self.weight}


@dataclass(slots=True)
class TemplateSpec:
    """Specification loaded from a template's spec.yaml."""

    name: str
    description: str
    task_prompt: str
    judge_rubric: str
    output_format: str = "free_text"
    judge_model: str = "claude-sonnet-4-20250514"
    max_rounds: int = 1
    quality_threshold: float = 0.9
    reference_context: str | None = None
    required_concepts: list[str] | None = None
    calibration_examples: list[dict[str, Any]] | None = None
    revision_prompt: str | None = None
    sample_input: str | None = None
    rubric_dimensions: list[RubricDimension] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemplateSpec:
        """Create a TemplateSpec from a dictionary (parsed YAML)."""
        dims_data = data.get("rubric_dimensions")
        dims: list[RubricDimension] | None = None
        if dims_data:
            dims = [RubricDimension.from_dict(d) for d in dims_data]
        return cls(
            name=data["name"],
            description=data["description"],
            task_prompt=data["task_prompt"],
            judge_rubric=data["judge_rubric"],
            output_format=data.get("output_format", "free_text"),
            judge_model=data.get("judge_model", "claude-sonnet-4-20250514"),
            max_rounds=data.get("max_rounds", 1),
            quality_threshold=data.get("quality_threshold", 0.9),
            reference_context=data.get("reference_context"),
            required_concepts=data.get("required_concepts"),
            calibration_examples=data.get("calibration_examples"),
            revision_prompt=data.get("revision_prompt"),
            sample_input=data.get("sample_input"),
            rubric_dimensions=dims,
        )

    def to_agent_task_spec(self) -> AgentTaskSpec:
        """Convert this template spec to an AgentTaskSpec."""
        return AgentTaskSpec(
            task_prompt=self.task_prompt,
            judge_rubric=self.judge_rubric,
            output_format=self.output_format,
            judge_model=self.judge_model,
            max_rounds=self.max_rounds,
            quality_threshold=self.quality_threshold,
            reference_context=self.reference_context,
            required_concepts=self.required_concepts,
            calibration_examples=self.calibration_examples,
            revision_prompt=self.revision_prompt,
            sample_input=self.sample_input,
        )


class _TemplateAgentTask(AgentTaskInterface):
    """A concrete AgentTaskInterface backed by a TemplateSpec.

    Provides a deterministic evaluate_output that uses simple keyword
    heuristics so templates work without an LLM provider.
    """

    def __init__(self, spec: TemplateSpec) -> None:
        self._spec = spec

    def get_task_prompt(self, state: dict[str, Any]) -> str:
        """Return the task prompt for the agent."""
        return self._spec.task_prompt

    def evaluate_output(
        self,
        output: str,
        state: dict[str, Any],
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict[str, Any]] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        """Simple heuristic evaluation for smoke testing without an LLM."""
        score = min(1.0, max(0.1, len(output) / 500.0))
        dims: dict[str, float] = {}
        if self._spec.rubric_dimensions:
            for dim in self._spec.rubric_dimensions:
                dims[dim.name] = score
        return AgentTaskResult(
            score=score,
            reasoning="Heuristic evaluation based on output length",
            dimension_scores=dims,
        )

    def get_rubric(self) -> str:
        """Return the evaluation rubric."""
        return self._spec.judge_rubric

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        """Return the initial state for this task."""
        return {"seed": seed or 0, "template": self._spec.name}

    def describe_task(self) -> str:
        """Return a human-readable description of the task."""
        return self._spec.description


class TemplateLoader:
    """Loads and manages scenario templates."""

    def __init__(self, template_dir: Path | None = None) -> None:
        self._template_dir = template_dir or TEMPLATE_DIR

    def list_templates(self) -> list[TemplateSpec]:
        """List all available templates."""
        templates: list[TemplateSpec] = []
        for entry in sorted(self._template_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            spec_file = entry / "spec.yaml"
            if not spec_file.is_file():
                continue
            data = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                templates.append(TemplateSpec.from_dict(data))
        return templates

    def get_template(self, name: str) -> TemplateSpec:
        """Get a specific template by name. Raises KeyError if not found."""
        template_path = self._template_dir / name
        spec_file = template_path / "spec.yaml"
        if not spec_file.is_file():
            raise KeyError(f"Template '{name}' not found in {self._template_dir}")
        data = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
        return TemplateSpec.from_dict(data)

    def load_as_agent_task(self, template_name: str, scenario_name: str | None = None) -> AgentTaskInterface:
        """Load a template as a concrete AgentTaskInterface instance."""
        spec = self.get_template(template_name)
        return _TemplateAgentTask(spec)

    def scaffold(
        self,
        template_name: str,
        target_dir: Path,
        overrides: dict[str, Any] | None = None,
    ) -> Path:
        """Copy template files to a target directory and generate agent_task.py.

        Args:
            template_name: Name of the template to scaffold from.
            target_dir: Directory to write the scaffolded scenario into.
            overrides: Optional dict of spec fields to override.

        Returns:
            The target directory path.
        """
        spec = self.get_template(template_name)
        source_dir = self._template_dir / template_name

        target_dir.mkdir(parents=True, exist_ok=True)

        # Copy template files
        for f in ("spec.yaml", "README.md", "example_input.json", "example_output.json"):
            src = source_dir / f
            if src.is_file():
                shutil.copy2(src, target_dir / f)

        # Apply overrides to spec if provided
        if overrides:
            spec_path = target_dir / "spec.yaml"
            data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            data.update(overrides)
            spec_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")

        # Generate agent_task.py
        self._generate_agent_task_module(spec, target_dir)

        # Write scenario_type.txt marker
        (target_dir / "scenario_type.txt").write_text("agent_task", encoding="utf-8")

        return target_dir

    def _generate_agent_task_module(self, spec: TemplateSpec, target_dir: Path) -> None:
        """Generate a Python module implementing AgentTaskInterface for the template."""
        rubric_escaped = spec.judge_rubric.replace('"""', r'\"\"\"')
        prompt_escaped = spec.task_prompt.replace('"""', r'\"\"\"')
        desc_escaped = spec.description.replace('"""', r'\"\"\"')

        # Build dimension scores code
        if spec.rubric_dimensions:
            dim_entries = ", ".join(
                f'"{d.name}": score' for d in spec.rubric_dimensions
            )
            dim_code = f"        dimension_scores = {{{dim_entries}}}"
        else:
            dim_code = "        dimension_scores = {}"

        source = f'''"""Auto-generated agent task from template: {spec.name}."""
from __future__ import annotations

from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class TemplateAgentTask(AgentTaskInterface):
    """Agent task generated from the {spec.name} template."""

    def get_task_prompt(self, state: dict) -> str:
        return """{prompt_escaped}"""

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        score = min(1.0, max(0.1, len(output) / 500.0))
{dim_code}
        return AgentTaskResult(
            score=score,
            reasoning="Heuristic evaluation based on output length",
            dimension_scores=dimension_scores,
        )

    def get_rubric(self) -> str:
        return """{rubric_escaped}"""

    def initial_state(self, seed: int | None = None) -> dict:
        return {{"seed": seed or 0, "template": "{spec.name}"}}

    def describe_task(self) -> str:
        return """{desc_escaped}"""
'''
        (target_dir / "agent_task.py").write_text(source, encoding="utf-8")


__all__ = [
    "TEMPLATE_DIR",
    "RubricDimension",
    "TemplateLoader",
    "TemplateSpec",
]
