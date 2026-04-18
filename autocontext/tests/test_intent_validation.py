"""Tests for AC-242: Scenario-intent validation for natural-language generated tasks.

Ensures the generated spec matches the user's original intent before
accepting it, catching task-family drift early.
"""

from __future__ import annotations

import pytest

from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
from autocontext.scenarios.custom.agent_task_validator import validate_intent

# ---------------------------------------------------------------------------
# Task-family keyword extraction
# ---------------------------------------------------------------------------


class TestIntentKeywordOverlap:
    def test_matching_intent_passes(self) -> None:
        """A spec about Python code quality should pass for a code quality description."""
        errors = validate_intent(
            user_description="Evaluate Python code quality and correctness",
            spec=AgentTaskSpec(
                task_prompt="Evaluate the given Python code for quality, readability, and correctness.",
                judge_rubric="Score code quality on correctness, style, and efficiency.",
            ),
        )
        assert errors == []

    def test_complete_domain_drift_detected(self) -> None:
        """A debugging description producing a cooking spec should be caught."""
        errors = validate_intent(
            user_description="Root cause analysis of server crashes with red herrings",
            spec=AgentTaskSpec(
                task_prompt="Write a detailed recipe for chocolate cake with frosting techniques.",
                judge_rubric="Evaluate recipe completeness, ingredient accuracy, and presentation.",
            ),
        )
        assert len(errors) > 0
        assert any("intent" in e.lower() or "drift" in e.lower() or "mismatch" in e.lower() for e in errors)

    def test_subtle_drift_detected(self) -> None:
        """A debugging description that gets a writing task about microservices."""
        errors = validate_intent(
            user_description="Stateful debugging with investigative sequences and red herrings",
            spec=AgentTaskSpec(
                task_prompt="Write an essay about microservices architecture trade-offs and design patterns.",
                judge_rubric="Evaluate essay structure, argument quality, and technical depth.",
            ),
        )
        assert len(errors) > 0

    def test_closely_related_terms_pass(self) -> None:
        """Synonyms / closely related terms should not trigger false positives."""
        errors = validate_intent(
            user_description="Build a sentiment analysis classifier for customer reviews",
            spec=AgentTaskSpec(
                task_prompt="Classify the sentiment of the given customer review as positive, negative, or neutral.",
                judge_rubric="Score accuracy of sentiment classification and reasoning quality.",
            ),
        )
        assert errors == []

    def test_biomedical_agent_task_prompt_does_not_false_positive_as_code(self) -> None:
        """Biomedical evaluation prompts should not drift just because they mention kidney function."""
        errors = validate_intent(
            user_description=(
                "Build and run a pharmacological reasoning scenario where the agent predicts "
                "drug interaction risks.\n\n"
                "Use agent-task evaluation with structured output:\n"
                "* Agent receives: patient profile (age, weight, conditions, current medications, "
                "liver/kidney function), proposed new medication\n"
                "* Agent must produce: interaction risk assessment with mechanism explanation, "
                "severity rating, clinical recommendation\n"
                "* Evaluation dimensions: interaction identification accuracy, mechanism explanation "
                "quality, severity rating accuracy, clinical recommendation quality"
            ),
            spec=AgentTaskSpec(
                task_prompt=(
                    "Assess the proposed medication against the patient profile, identify clinically "
                    "meaningful drug interactions, explain the mechanism, assign a severity rating, "
                    "and recommend the safest next step."
                ),
                judge_rubric=(
                    "Score interaction identification accuracy, mechanism explanation quality, "
                    "severity rating accuracy, and clinical recommendation quality."
                ),
                output_format="json_schema",
            ),
        )
        assert errors == []

    def test_meta_learning_summary_prompt_does_not_false_positive_as_data_task(self) -> None:
        """Meta-learning prompts should not be rejected just because they mention learning or self-models."""
        errors = validate_intent(
            user_description=(
                "The system's own generation history is fed back as input. It must produce a compressed summary of what it "
                "has learned, then use that summary as the only context for the next generation."
            ),
            spec=AgentTaskSpec(
                task_prompt=(
                    "Summarize the most important lessons from the prior generations into a compact memory note that can guide "
                    "the next attempt without access to the raw history."
                ),
                judge_rubric=(
                    "Score whether the summary preserves actionable lessons, compresses redundant detail, and supports strong "
                    "next-generation performance."
                ),
            ),
        )
        assert errors == []


# ---------------------------------------------------------------------------
# Rubric-prompt coherence
# ---------------------------------------------------------------------------


class TestRubricPromptCoherence:
    def test_coherent_rubric_passes(self) -> None:
        """Rubric about code quality matches a code quality task."""
        errors = validate_intent(
            user_description="Evaluate code quality",
            spec=AgentTaskSpec(
                task_prompt="Review the provided code for quality and bugs.",
                judge_rubric="Score code correctness, readability, and maintainability.",
            ),
        )
        assert errors == []

    def test_rubric_about_wrong_domain(self) -> None:
        """Rubric about literary quality for a code task should be flagged."""
        errors = validate_intent(
            user_description="Evaluate Python code for bugs",
            spec=AgentTaskSpec(
                task_prompt="Review the provided Python code for correctness.",
                judge_rubric="Score literary quality, prose style, and narrative flow.",
            ),
        )
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Output format compatibility
# ---------------------------------------------------------------------------


class TestOutputFormatCompatibility:
    def test_code_format_for_code_task(self) -> None:
        """output_format='code' is fine for a code generation task."""
        errors = validate_intent(
            user_description="Generate a Python sorting algorithm",
            spec=AgentTaskSpec(
                task_prompt="Write a Python function that implements merge sort.",
                judge_rubric="Score code correctness and efficiency.",
                output_format="code",
            ),
        )
        assert errors == []

    def test_code_format_for_writing_task(self) -> None:
        """output_format='code' for a writing task should be flagged."""
        errors = validate_intent(
            user_description="Write a persuasive essay about climate change",
            spec=AgentTaskSpec(
                task_prompt="Write a persuasive essay about climate change impacts.",
                judge_rubric="Score argument quality and persuasiveness.",
                output_format="code",
            ),
        )
        assert len(errors) > 0
        assert any("format" in e.lower() for e in errors)

    def test_free_text_for_code_task(self) -> None:
        """output_format='free_text' for a code generation task should be flagged."""
        errors = validate_intent(
            user_description="Generate Python code for a web scraper",
            spec=AgentTaskSpec(
                task_prompt="Write a Python web scraper using requests and BeautifulSoup.",
                judge_rubric="Score code correctness and completeness.",
                output_format="free_text",
            ),
        )
        assert len(errors) > 0
        assert any("format" in e.lower() for e in errors)

    def test_json_schema_for_structured_task(self) -> None:
        """output_format='json_schema' is valid when the task explicitly asks for JSON."""
        errors = validate_intent(
            user_description="Return a JSON schema with fields severity, owner, and next_steps",
            spec=AgentTaskSpec(
                task_prompt="Summarize the incident as a JSON object with severity, owner, and next_steps fields.",
                judge_rubric="Score schema completeness, field accuracy, and machine readability.",
                output_format="json_schema",
            ),
        )
        assert errors == []

    def test_free_text_for_structured_task(self) -> None:
        """A task that explicitly asks for JSON should reject free_text output."""
        errors = validate_intent(
            user_description="Produce a machine-readable JSON response with fields title and score",
            spec=AgentTaskSpec(
                task_prompt="Write a short summary of the result and mention the score.",
                judge_rubric="Score clarity and coverage.",
                output_format="free_text",
            ),
        )
        assert len(errors) > 0
        assert any("json" in e.lower() or "structured" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Name coherence (derived name vs spec content)
# ---------------------------------------------------------------------------


class TestNameCoherence:
    def test_derived_name_preserves_domain_concepts(self) -> None:
        """Key domain terms from the description should appear in the spec."""
        # "debugging" is the key domain concept
        errors = validate_intent(
            user_description="Interactive debugging simulation with log analysis",
            spec=AgentTaskSpec(
                task_prompt="Analyze server logs to find the root cause of the error.",
                judge_rubric="Score diagnostic accuracy and investigative thoroughness.",
            ),
        )
        # "debugging" concept is semantically preserved via "logs" and "root cause"
        # and "diagnostic" — this should pass
        assert errors == []

    def test_all_domain_terms_missing_from_spec(self) -> None:
        """If no key domain terms survive into the spec, flag it."""
        errors = validate_intent(
            user_description="Quantum computing circuit optimization",
            spec=AgentTaskSpec(
                task_prompt="Write a blog post about healthy eating habits.",
                judge_rubric="Score nutritional accuracy and writing quality.",
            ),
        )
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_description_passes(self) -> None:
        """An empty description can't have intent to validate."""
        errors = validate_intent(
            user_description="",
            spec=AgentTaskSpec(
                task_prompt="Do something.",
                judge_rubric="Evaluate quality.",
            ),
        )
        assert errors == []

    def test_very_short_description(self) -> None:
        """A very short description should still be compared."""
        errors = validate_intent(
            user_description="haiku",
            spec=AgentTaskSpec(
                task_prompt="Write a haiku about nature.",
                judge_rubric="Evaluate syllable structure and imagery.",
            ),
        )
        assert errors == []

    def test_matching_with_extra_spec_details(self) -> None:
        """Spec can be more detailed than description without triggering drift."""
        errors = validate_intent(
            user_description="API documentation generator",
            spec=AgentTaskSpec(
                task_prompt=(
                    "Generate comprehensive API documentation for the provided endpoints. "
                    "Include request/response schemas, authentication requirements, "
                    "error codes, and usage examples."
                ),
                judge_rubric="Score documentation completeness, accuracy, and clarity.",
            ),
        )
        assert errors == []


# ---------------------------------------------------------------------------
# Integration with AgentTaskCreator
# ---------------------------------------------------------------------------


class TestCreatorIntentValidation:
    def test_creator_calls_validate_intent(self) -> None:
        """AgentTaskCreator.create() should call validate_intent before codegen."""
        from unittest.mock import MagicMock, patch

        from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator

        creator = AgentTaskCreator(
            llm_fn=MagicMock(return_value="dummy"),
            knowledge_root=MagicMock(),
        )

        bad_spec = AgentTaskSpec(
            task_prompt="Write a recipe for chocolate cake.",
            judge_rubric="Evaluate recipe quality.",
        )

        with (
            patch(
                "autocontext.scenarios.custom.agent_task_creator.design_agent_task",
                return_value=bad_spec,
            ),
            patch(
                "autocontext.scenarios.custom.agent_task_creator.validate_for_family",
                return_value=[],
            ),
            patch(
                "autocontext.scenarios.custom.agent_task_creator.validate_intent",
                return_value=["intent mismatch: task-family drift detected"],
            ) as mock_intent,
        ):
            with pytest.raises(ValueError, match="intent"):
                creator.create("Write a concise abstract summarizing a research paper")
            mock_intent.assert_called_once()
