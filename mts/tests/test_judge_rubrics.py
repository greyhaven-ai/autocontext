"""Tests for AC-207: Domain-specific judge rubrics for shipped templates."""
from __future__ import annotations

import json
from unittest.mock import patch

import yaml

from mts.execution.judge import LLMJudge
from mts.providers.base import CompletionResult, LLMProvider
from mts.scenarios.templates import TEMPLATE_DIR, RubricDimension, TemplateLoader, TemplateSpec


class _ConditionalProvider(LLMProvider):
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        if "strong candidate output" in user_prompt:
            payload = {
                "score": 0.92,
                "reasoning": "Strong candidate output",
                "dimensions": {
                    "clarity": 0.9,
                    "specificity": 0.95,
                    "constraint_coverage": 0.92,
                    "format_compliance": 0.91,
                    "edge_case_handling": 0.89,
                },
            }
        else:
            payload = {
                "score": 0.41,
                "reasoning": "Weak candidate output",
                "dimensions": {
                    "clarity": 0.4,
                    "specificity": 0.42,
                    "constraint_coverage": 0.39,
                    "format_compliance": 0.43,
                    "edge_case_handling": 0.41,
                },
            }
        return CompletionResult(
            text=(
                "<!-- JUDGE_RESULT_START -->\n"
                f"{json.dumps(payload)}\n"
                "<!-- JUDGE_RESULT_END -->"
            ),
            model=model or self.default_model(),
        )

    def default_model(self) -> str:
        return "test-model"

# ---------------------------------------------------------------------------
# Rubric YAML schema tests
# ---------------------------------------------------------------------------


class TestRubricSchema:
    """Verify rubric dimension YAML schema is well-defined."""

    def test_rubric_dimension_from_dict(self) -> None:
        data = {"name": "clarity", "description": "Is it clear?", "weight": 0.3}
        dim = RubricDimension.from_dict(data)
        assert dim.name == "clarity"
        assert dim.description == "Is it clear?"
        assert dim.weight == 0.3

    def test_rubric_dimension_default_weight(self) -> None:
        data = {"name": "accuracy", "description": "Is it accurate?"}
        dim = RubricDimension.from_dict(data)
        assert dim.weight == 1.0

    def test_rubric_dimension_to_dict(self) -> None:
        dim = RubricDimension(name="test", description="Test dim", weight=0.5)
        d = dim.to_dict()
        assert d == {"name": "test", "description": "Test dim", "weight": 0.5}

    def test_rubric_dimensions_weights_sum(self) -> None:
        """Weights for each template's rubric dimensions should sum to approximately 1.0."""
        loader = TemplateLoader()
        for template in loader.list_templates():
            if template.rubric_dimensions:
                total = sum(d.weight for d in template.rubric_dimensions)
                assert abs(total - 1.0) < 0.01, (
                    f"Template '{template.name}' rubric weights sum to {total}, expected ~1.0"
                )


# ---------------------------------------------------------------------------
# Per-template rubric validation
# ---------------------------------------------------------------------------


class TestPromptOptimizationRubric:
    """Validate prompt-optimization template rubric."""

    def test_has_rubric_dimensions(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("prompt-optimization")
        assert spec.rubric_dimensions is not None
        assert len(spec.rubric_dimensions) >= 5

    def test_required_dimensions(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("prompt-optimization")
        assert spec.rubric_dimensions is not None
        dim_names = [d.name for d in spec.rubric_dimensions]
        assert "clarity" in dim_names
        assert "specificity" in dim_names
        assert "constraint_coverage" in dim_names
        assert "format_compliance" in dim_names
        assert "edge_case_handling" in dim_names

    def test_dimension_descriptions_nonempty(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("prompt-optimization")
        assert spec.rubric_dimensions is not None
        for dim in spec.rubric_dimensions:
            assert len(dim.description) > 0, f"Dimension '{dim.name}' has empty description"


class TestRagAccuracyRubric:
    """Validate rag-accuracy template rubric."""

    def test_has_rubric_dimensions(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("rag-accuracy")
        assert spec.rubric_dimensions is not None
        assert len(spec.rubric_dimensions) >= 5

    def test_required_dimensions(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("rag-accuracy")
        assert spec.rubric_dimensions is not None
        dim_names = [d.name for d in spec.rubric_dimensions]
        assert "retrieval_relevance" in dim_names
        assert "answer_grounding" in dim_names
        assert "citation_accuracy" in dim_names
        assert "hallucination_detection" in dim_names

    def test_dimension_descriptions_nonempty(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("rag-accuracy")
        assert spec.rubric_dimensions is not None
        for dim in spec.rubric_dimensions:
            assert len(dim.description) > 0, f"Dimension '{dim.name}' has empty description"


class TestContentGenerationRubric:
    """Validate content-generation template rubric."""

    def test_has_rubric_dimensions(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("content-generation")
        assert spec.rubric_dimensions is not None
        assert len(spec.rubric_dimensions) >= 5

    def test_required_dimensions(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("content-generation")
        assert spec.rubric_dimensions is not None
        dim_names = [d.name for d in spec.rubric_dimensions]
        assert "readability" in dim_names
        assert "engagement" in dim_names
        assert "factual_accuracy" in dim_names
        assert "structure" in dim_names
        assert "keyword_integration" in dim_names

    def test_dimension_descriptions_nonempty(self) -> None:
        loader = TemplateLoader()
        spec = loader.get_template("content-generation")
        assert spec.rubric_dimensions is not None
        for dim in spec.rubric_dimensions:
            assert len(dim.description) > 0, f"Dimension '{dim.name}' has empty description"


# ---------------------------------------------------------------------------
# LLMJudge integration with rubric dimensions
# ---------------------------------------------------------------------------


class TestJudgeRubricIntegration:
    """Verify LLMJudge can read and use rubric dimensions from templates."""

    def _make_judge_response(self, score: float, dimensions: dict[str, float]) -> str:
        """Build a mock judge response with markers."""
        payload = json.dumps({"score": score, "reasoning": "Test evaluation", "dimensions": dimensions})
        return f"<!-- JUDGE_RESULT_START -->\n{payload}\n<!-- JUDGE_RESULT_END -->"

    def test_judge_with_template_rubric(self) -> None:
        """LLMJudge should accept a rubric from a template spec."""
        loader = TemplateLoader()
        spec = loader.get_template("prompt-optimization")

        # Create a mock LLM that returns a scored response with dimensions
        dim_scores: dict[str, float] = {}
        if spec.rubric_dimensions:
            dim_scores = {d.name: 0.8 for d in spec.rubric_dimensions}

        response = self._make_judge_response(0.85, dim_scores)

        def mock_llm(system: str, user: str) -> str:
            return response

        judge = LLMJudge(
            model="test-model",
            rubric=spec.judge_rubric,
            llm_fn=mock_llm,
        )
        result = judge.evaluate(
            task_prompt=spec.task_prompt,
            agent_output="You are an expert summarizer. Format as 3-5 bullet points.",
        )
        assert 0.0 <= result.score <= 1.0
        assert result.score == 0.85

    def test_judge_with_pinned_dimensions_from_rubric(self) -> None:
        """LLMJudge with pinned_dimensions should constrain the dimension names."""
        loader = TemplateLoader()
        spec = loader.get_template("content-generation")
        assert spec.rubric_dimensions is not None
        dim_names = [d.name for d in spec.rubric_dimensions]

        dim_scores = {name: 0.75 for name in dim_names}
        response = self._make_judge_response(0.78, dim_scores)

        def mock_llm(system: str, user: str) -> str:
            return response

        judge = LLMJudge(
            model="test-model",
            rubric=spec.judge_rubric,
            llm_fn=mock_llm,
        )
        result = judge.evaluate(
            task_prompt=spec.task_prompt,
            agent_output="A blog post about microservices architecture...",
            pinned_dimensions=dim_names,
        )
        assert result.score == 0.78
        for name in dim_names:
            assert name in result.dimension_scores

    def test_multi_dimensional_scoring_composite(self) -> None:
        """Rubric dimensions with weights should enable composite scoring."""
        loader = TemplateLoader()
        spec = loader.get_template("rag-accuracy")
        assert spec.rubric_dimensions is not None

        # Build dimension scores that vary
        dim_scores = {
            "retrieval_relevance": 0.9,
            "answer_grounding": 0.7,
            "citation_accuracy": 0.6,
            "hallucination_detection": 0.8,
            "parameter_justification": 0.85,
        }

        # Compute expected weighted score
        expected = sum(
            dim_scores[d.name] * d.weight
            for d in spec.rubric_dimensions
            if d.name in dim_scores
        )

        response = self._make_judge_response(expected, dim_scores)

        def mock_llm(system: str, user: str) -> str:
            return response

        judge = LLMJudge(
            model="test-model",
            rubric=spec.judge_rubric,
            llm_fn=mock_llm,
        )
        result = judge.evaluate(
            task_prompt=spec.task_prompt,
            agent_output="Configuration with chunk_size=256...",
        )
        assert abs(result.score - expected) < 0.01

    def test_score_variance_is_meaningful(self) -> None:
        """Rubric dimensions should produce meaningful score variance across different outputs."""
        loader = TemplateLoader()
        task = loader.load_as_agent_task("prompt-optimization")

        with patch("mts.scenarios.templates.get_provider", return_value=_ConditionalProvider()):
            weak_result = task.evaluate_output("weak candidate output", {})
            strong_result = task.evaluate_output("strong candidate output", {})

        # Scores should differ for different outputs
        assert weak_result.score != strong_result.score
        assert strong_result.score > weak_result.score
        assert sorted(strong_result.dimension_scores.keys()) == [
            "clarity",
            "constraint_coverage",
            "edge_case_handling",
            "format_compliance",
            "specificity",
        ]


# ---------------------------------------------------------------------------
# Rubric YAML consistency across templates
# ---------------------------------------------------------------------------


class TestRubricConsistency:
    """Ensure all shipped templates have consistent rubric structure."""

    def test_all_templates_have_rubric_dimensions(self) -> None:
        loader = TemplateLoader()
        for template in loader.list_templates():
            assert template.rubric_dimensions is not None, (
                f"Template '{template.name}' is missing rubric_dimensions"
            )
            assert len(template.rubric_dimensions) >= 3, (
                f"Template '{template.name}' has fewer than 3 rubric dimensions"
            )

    def test_all_dimensions_have_valid_weights(self) -> None:
        loader = TemplateLoader()
        for template in loader.list_templates():
            if template.rubric_dimensions:
                for dim in template.rubric_dimensions:
                    assert 0.0 < dim.weight <= 1.0, (
                        f"Template '{template.name}', dim '{dim.name}': "
                        f"weight {dim.weight} out of range (0, 1]"
                    )

    def test_rubric_yaml_roundtrip(self) -> None:
        """Rubric dimensions should survive YAML round-trip."""
        loader = TemplateLoader()
        for template in loader.list_templates():
            spec_path = TEMPLATE_DIR / template.name / "spec.yaml"
            data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            reloaded = TemplateSpec.from_dict(data)
            assert reloaded.rubric_dimensions is not None
            assert len(reloaded.rubric_dimensions) == len(template.rubric_dimensions or [])
            for orig, reloaded_dim in zip(
                template.rubric_dimensions or [], reloaded.rubric_dimensions, strict=True,
            ):
                assert orig.name == reloaded_dim.name
                assert orig.weight == reloaded_dim.weight
