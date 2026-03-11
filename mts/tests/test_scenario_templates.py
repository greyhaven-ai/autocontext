"""Tests for AC-205: Scenario template library."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from mts.providers.base import CompletionResult, LLMProvider
from mts.scenarios.templates import TEMPLATE_DIR, TemplateLoader, TemplateSpec


def _judge_response(score: float, dimensions: dict[str, float]) -> str:
    payload = json.dumps({
        "score": score,
        "reasoning": "Template smoke test",
        "dimensions": dimensions,
    })
    return f"<!-- JUDGE_RESULT_START -->\n{payload}\n<!-- JUDGE_RESULT_END -->"


class _StaticProvider(LLMProvider):
    def __init__(self, response: str) -> None:
        self._response = response

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        return CompletionResult(text=self._response, model=model or self.default_model())

    def default_model(self) -> str:
        return "test-model"

# ---------------------------------------------------------------------------
# Template directory structure tests
# ---------------------------------------------------------------------------


class TestTemplateDirectoryStructure:
    """Verify that the templates/ directory has the expected layout."""

    def test_template_dir_exists(self) -> None:
        assert TEMPLATE_DIR.is_dir(), f"Template directory should exist at {TEMPLATE_DIR}"

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_template_subdirectory_exists(self, template_name: str) -> None:
        template_path = TEMPLATE_DIR / template_name
        assert template_path.is_dir(), f"Template '{template_name}' directory should exist"

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_template_has_spec_yaml(self, template_name: str) -> None:
        spec_path = TEMPLATE_DIR / template_name / "spec.yaml"
        assert spec_path.is_file(), f"Template '{template_name}' should have spec.yaml"

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_template_has_readme(self, template_name: str) -> None:
        readme_path = TEMPLATE_DIR / template_name / "README.md"
        assert readme_path.is_file(), f"Template '{template_name}' should have README.md"

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_template_has_example_input(self, template_name: str) -> None:
        input_path = TEMPLATE_DIR / template_name / "example_input.json"
        assert input_path.is_file(), f"Template '{template_name}' should have example_input.json"

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_template_has_example_output(self, template_name: str) -> None:
        output_path = TEMPLATE_DIR / template_name / "example_output.json"
        assert output_path.is_file(), f"Template '{template_name}' should have example_output.json"


# ---------------------------------------------------------------------------
# TemplateSpec tests
# ---------------------------------------------------------------------------


class TestTemplateSpec:
    """Verify TemplateSpec dataclass and YAML parsing."""

    def test_template_spec_from_yaml(self) -> None:
        yaml_data = {
            "name": "test-template",
            "description": "A test template",
            "task_prompt": "Do something",
            "judge_rubric": "Evaluate the output",
            "output_format": "free_text",
            "judge_model": "claude-sonnet-4-20250514",
        }
        spec = TemplateSpec.from_dict(yaml_data)
        assert spec.name == "test-template"
        assert spec.description == "A test template"
        assert spec.task_prompt == "Do something"
        assert spec.judge_rubric == "Evaluate the output"
        assert spec.output_format == "free_text"

    def test_template_spec_defaults(self) -> None:
        yaml_data = {
            "name": "minimal",
            "description": "Minimal template",
            "task_prompt": "Do X",
            "judge_rubric": "Check X",
        }
        spec = TemplateSpec.from_dict(yaml_data)
        assert spec.output_format == "free_text"
        assert spec.judge_model == "claude-sonnet-4-20250514"
        assert spec.max_rounds == 1
        assert spec.quality_threshold == 0.9

    def test_template_spec_optional_fields(self) -> None:
        yaml_data = {
            "name": "full",
            "description": "Full template",
            "task_prompt": "Task",
            "judge_rubric": "Rubric",
            "reference_context": "Some context",
            "required_concepts": ["concept1", "concept2"],
            "max_rounds": 3,
            "quality_threshold": 0.85,
            "revision_prompt": "Improve your output",
        }
        spec = TemplateSpec.from_dict(yaml_data)
        assert spec.reference_context == "Some context"
        assert spec.required_concepts == ["concept1", "concept2"]
        assert spec.max_rounds == 3
        assert spec.quality_threshold == 0.85
        assert spec.revision_prompt == "Improve your output"

    def test_template_spec_to_agent_task_spec(self) -> None:
        yaml_data = {
            "name": "converter-test",
            "description": "Test conversion",
            "task_prompt": "Do stuff",
            "judge_rubric": "Score it",
            "max_rounds": 2,
        }
        spec = TemplateSpec.from_dict(yaml_data)
        ats = spec.to_agent_task_spec()
        assert ats.task_prompt == "Do stuff"
        assert ats.judge_rubric == "Score it"
        assert ats.max_rounds == 2


# ---------------------------------------------------------------------------
# TemplateLoader tests
# ---------------------------------------------------------------------------


class TestTemplateLoader:
    """Test the TemplateLoader class."""

    def test_list_templates(self) -> None:
        loader = TemplateLoader()
        templates = loader.list_templates()
        assert len(templates) >= 3
        names = [t.name for t in templates]
        assert "prompt-optimization" in names
        assert "rag-accuracy" in names
        assert "content-generation" in names

    def test_get_template(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("prompt-optimization")
        assert spec is not None
        assert spec.name == "prompt-optimization"
        assert spec.task_prompt  # non-empty
        assert spec.judge_rubric  # non-empty

    def test_get_template_not_found(self) -> None:
        loader = TemplateLoader()
        with pytest.raises(KeyError):
            loader.get_template("nonexistent-template")

    def test_load_template_creates_agent_task(self, tmp_path: Path) -> None:
        """Loading a template should create an AgentTaskInterface-compatible scenario."""
        loader = TemplateLoader()
        task = loader.load_as_agent_task("prompt-optimization", scenario_name="test-prompt-opt")
        assert task is not None
        # Verify it implements the AgentTaskInterface methods
        state = task.initial_state()
        assert isinstance(state, dict)
        prompt = task.get_task_prompt(state)
        assert isinstance(prompt, str) and len(prompt) > 0
        rubric = task.get_rubric()
        assert isinstance(rubric, str) and len(rubric) > 0
        desc = task.describe_task()
        assert isinstance(desc, str)
        assert state["task_name"] == "test-prompt-opt"

    def test_scaffold_to_directory(self, tmp_path: Path) -> None:
        """Scaffolding a template should copy files to a target directory."""
        loader = TemplateLoader()
        target = tmp_path / "my-scenario"
        loader.scaffold(template_name="rag-accuracy", target_dir=target)
        assert (target / "spec.yaml").is_file()
        assert (target / "README.md").is_file()
        assert (target / "example_input.json").is_file()
        assert (target / "example_output.json").is_file()
        assert (target / "agent_task.py").is_file()
        assert (target / "scenario_type.txt").read_text().strip() == "agent_task"
        source = (target / "agent_task.py").read_text(encoding="utf-8")
        assert "LLMJudge" in source
        assert "get_provider" in source


# ---------------------------------------------------------------------------
# Spec YAML validation (each shipped template)
# ---------------------------------------------------------------------------


class TestShippedTemplateSpecs:
    """Validate that each shipped template has a well-formed spec.yaml."""

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_spec_yaml_parses(self, template_name: str) -> None:
        spec_path = TEMPLATE_DIR / template_name / "spec.yaml"
        data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        spec = TemplateSpec.from_dict(data)
        assert spec.name == template_name

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_spec_has_required_fields(self, template_name: str) -> None:
        spec_path = TEMPLATE_DIR / template_name / "spec.yaml"
        data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        assert "name" in data
        assert "description" in data
        assert "task_prompt" in data
        assert "judge_rubric" in data

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_example_input_is_valid_json(self, template_name: str) -> None:
        path = TEMPLATE_DIR / template_name / "example_input.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_example_output_is_valid_json(self, template_name: str) -> None:
        path = TEMPLATE_DIR / template_name / "example_output.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Smoke test: template produces usable agent task
# ---------------------------------------------------------------------------


class TestTemplateSmoke:
    """Smoke tests verifying templates are functional with deterministic evaluation."""

    @pytest.mark.parametrize("template_name", ["prompt-optimization", "rag-accuracy", "content-generation"])
    def test_template_agent_task_smoke(self, template_name: str) -> None:
        """Each template should produce an agent task that can run initial_state + get_task_prompt + evaluate_output."""
        loader = TemplateLoader()
        task = loader.load_as_agent_task(template_name, scenario_name=f"smoke-{template_name}")
        state = task.initial_state(seed=42)
        prompt = task.get_task_prompt(state)
        assert len(prompt) > 0
        rubric = task.get_rubric()
        assert len(rubric) > 0
        # Evaluate with a dummy output
        spec = loader.get_template(template_name)
        dim_names = [d.name for d in spec.rubric_dimensions or []]
        response = _judge_response(0.74, {name: 0.74 for name in dim_names})
        with patch("mts.scenarios.templates.get_provider", return_value=_StaticProvider(response)):
            result = task.evaluate_output("Some output text for testing purposes.", state)
        assert 0.0 <= result.score <= 1.0
        assert isinstance(result.reasoning, str)
        assert sorted(result.dimension_scores.keys()) == sorted(dim_names)
