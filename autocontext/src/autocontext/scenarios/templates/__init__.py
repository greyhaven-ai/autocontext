"""Scenario template library for ready-to-use agent task scenarios."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from autocontext.config import load_settings
from autocontext.execution.judge import LLMJudge
from autocontext.providers.registry import get_provider
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

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
    judge_model: str = ""
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
            judge_model=data.get("judge_model", ""),
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
    """Concrete in-memory task implementation for a template."""

    def __init__(self, spec: TemplateSpec, *, scenario_name: str) -> None:
        self._spec = spec
        self.name = scenario_name

    def _pinned_dimensions(self) -> list[str] | None:
        if not self._spec.rubric_dimensions:
            return None
        return [dim.name for dim in self._spec.rubric_dimensions]

    def get_task_prompt(self, state: dict[str, Any]) -> str:
        """Return the task prompt for the agent."""
        prompt = self._spec.task_prompt
        if self._spec.sample_input:
            prompt += f"\n\n## Input Data\n{self._spec.sample_input}"
        return prompt

    def evaluate_output(
        self,
        output: str,
        state: dict[str, Any],
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict[str, Any]] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        """Evaluate the output with the configured judge provider."""
        settings = load_settings()
        provider = get_provider(settings)
        judge = LLMJudge(
            model=self._spec.judge_model,
            rubric=self._spec.judge_rubric,
            provider=provider,
        )
        result = judge.evaluate(
            task_prompt=self.get_task_prompt(state),
            agent_output=output,
            reference_context=reference_context or self._spec.reference_context,
            required_concepts=required_concepts or self._spec.required_concepts,
            calibration_examples=calibration_examples or self._spec.calibration_examples,
            pinned_dimensions=pinned_dimensions or self._pinned_dimensions(),
        )
        return AgentTaskResult(
            score=result.score,
            reasoning=result.reasoning,
            dimension_scores=result.dimension_scores,
            internal_retries=result.internal_retries,
        )

    def get_rubric(self) -> str:
        """Return the evaluation rubric."""
        return self._spec.judge_rubric

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        """Return the initial state for this task."""
        state: dict[str, Any] = {
            "seed": seed or 0,
            "task_name": self.name,
            "template": self._spec.name,
            "output_format": self._spec.output_format,
        }
        if self._spec.sample_input:
            state["sample_input"] = self._spec.sample_input
        return state

    def describe_task(self) -> str:
        """Return a human-readable description of the task."""
        return self._spec.description

    def prepare_context(self, state: dict[str, Any]) -> dict[str, Any]:
        if self._spec.reference_context:
            state["reference_context"] = self._spec.reference_context
        return state

    def revise_output(
        self,
        output: str,
        judge_result: AgentTaskResult,
        state: dict[str, Any],
    ) -> str:
        if not self._spec.revision_prompt and self._spec.max_rounds <= 1:
            return output

        settings = load_settings()
        provider = get_provider(settings)
        revision_instruction = self._spec.revision_prompt or (
            "Revise the following output based on the judge's feedback. "
            "Maintain what works and fix what does not."
        )
        prompt = (
            f"{revision_instruction}\n\n"
            f"## Original Output\n{output}\n\n"
            f"## Judge Score: {judge_result.score:.2f}\n"
            f"## Judge Feedback\n{judge_result.reasoning}\n\n"
            f"## Task\n{self.get_task_prompt(state)}\n\n"
            "Produce an improved version:"
        )
        result = provider.complete(
            system_prompt=(
                "You are revising content based on expert feedback. Improve the output. "
                "Return only the revised content."
            ),
            user_prompt=prompt,
            model=self._spec.judge_model,
        )
        return result.text


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
        return _TemplateAgentTask(spec, scenario_name=scenario_name or template_name)

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
        spec_data = yaml.safe_load((target_dir / "spec.yaml").read_text(encoding="utf-8"))
        if not isinstance(spec_data, dict):
            raise ValueError(f"Invalid template spec at {target_dir / 'spec.yaml'}")
        self._generate_agent_task_module(TemplateSpec.from_dict(spec_data), target_dir)

        # Write scenario_type.txt marker
        (target_dir / "scenario_type.txt").write_text("agent_task", encoding="utf-8")

        return target_dir

    def _generate_agent_task_module(self, spec: TemplateSpec, target_dir: Path) -> None:
        """Generate a Python module implementing AgentTaskInterface for the template."""
        rubric_escaped = spec.judge_rubric.replace('"""', r'\"\"\"')
        prompt_escaped = spec.task_prompt.replace('"""', r'\"\"\"')
        desc_escaped = spec.description.replace('"""', r'\"\"\"')
        sample_input_escaped = (spec.sample_input or "").replace('"""', r'\"\"\"')
        reference_context_escaped = (spec.reference_context or "").replace('"""', r'\"\"\"')
        revision_prompt_escaped = (spec.revision_prompt or "").replace('"""', r'\"\"\"')
        required_concepts_repr = repr(spec.required_concepts)
        calibration_examples_repr = repr(spec.calibration_examples)
        output_format_repr = repr(spec.output_format)
        judge_model_repr = repr(spec.judge_model)
        max_rounds_repr = repr(spec.max_rounds)
        quality_threshold_repr = repr(spec.quality_threshold)
        pinned_dimensions_repr = repr([dim.name for dim in spec.rubric_dimensions] if spec.rubric_dimensions else None)
        scenario_name_repr = repr(target_dir.name)

        source = f'''"""Auto-generated agent task from template: {spec.name}."""
from __future__ import annotations

from autocontext.config import load_settings
from autocontext.execution.judge import LLMJudge
from autocontext.providers.registry import get_provider
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class TemplateAgentTask(AgentTaskInterface):
    """Agent task generated from the {spec.name} template."""

    name = {scenario_name_repr}
    _description = """{desc_escaped}"""
    _task_prompt = """{prompt_escaped}"""
    _rubric = """{rubric_escaped}"""
    _output_format = {output_format_repr}
    _judge_model = {judge_model_repr}
    _max_rounds = {max_rounds_repr}
    _quality_threshold = {quality_threshold_repr}
    _reference_context = """{reference_context_escaped}"""
    _required_concepts = {required_concepts_repr}
    _calibration_examples = {calibration_examples_repr}
    _revision_prompt = """{revision_prompt_escaped}"""
    _sample_input = """{sample_input_escaped}"""
    _pinned_dimensions = {pinned_dimensions_repr}

    def get_task_prompt(self, state: dict) -> str:
        prompt = self._task_prompt
        if self._sample_input:
            prompt += "\\n\\n## Input Data\\n" + self._sample_input
        return prompt

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        settings = load_settings()
        provider = get_provider(settings)
        judge = LLMJudge(
            model=self._judge_model,
            rubric=self._rubric,
            provider=provider,
        )
        result = judge.evaluate(
            task_prompt=self.get_task_prompt(state),
            agent_output=output,
            reference_context=reference_context or (self._reference_context or None),
            required_concepts=required_concepts or self._required_concepts,
            calibration_examples=calibration_examples or self._calibration_examples,
            pinned_dimensions=pinned_dimensions or self._pinned_dimensions,
        )
        return AgentTaskResult(
            score=result.score,
            reasoning=result.reasoning,
            dimension_scores=result.dimension_scores,
            internal_retries=result.internal_retries,
        )

    def get_rubric(self) -> str:
        return self._rubric

    def initial_state(self, seed: int | None = None) -> dict:
        state = {{
            "seed": seed or 0,
            "task_name": self.name,
            "template": "{spec.name}",
            "output_format": self._output_format,
        }}
        if self._sample_input:
            state["sample_input"] = self._sample_input
        return state

    def describe_task(self) -> str:
        return self._description

    def prepare_context(self, state: dict) -> dict:
        if self._reference_context:
            state["reference_context"] = self._reference_context
        return state

    def revise_output(
        self,
        output: str,
        judge_result: AgentTaskResult,
        state: dict,
    ) -> str:
        if not self._revision_prompt and self._max_rounds <= 1:
            return output
        settings = load_settings()
        provider = get_provider(settings)
        revision_instruction = self._revision_prompt or (
            "Revise the following output based on the judge's feedback. "
            "Maintain what works and fix what does not."
        )
        prompt = (
            f"{{revision_instruction}}\\n\\n"
            f"## Original Output\\n{{output}}\\n\\n"
            f"## Judge Score: {{judge_result.score:.2f}}\\n"
            f"## Judge Feedback\\n{{judge_result.reasoning}}\\n\\n"
            f"## Task\\n{{self.get_task_prompt(state)}}\\n\\n"
            "Produce an improved version:"
        )
        result = provider.complete(
            system_prompt=(
                "You are revising content based on expert feedback. Improve the output. "
                "Return only the revised content."
            ),
            user_prompt=prompt,
            model=self._judge_model,
        )
        return result.text
'''
        (target_dir / "agent_task.py").write_text(source, encoding="utf-8")


__all__ = [
    "TEMPLATE_DIR",
    "RubricDimension",
    "TemplateLoader",
    "TemplateSpec",
]
