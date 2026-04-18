"""Tests for AC-309: auto-generate sample_input for data-referencing prompts.

Covers: needs_sample_input, generate_synthetic_sample_input, heal_spec_sample_input.
"""

from __future__ import annotations

# ===========================================================================
# needs_sample_input — detect when spec needs auto-heal
# ===========================================================================


class TestNeedsSampleInput:
    def test_detects_external_data_reference(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import needs_sample_input

        spec = AgentTaskSpec(
            task_prompt="You will be provided with customer data. Analyze it.",
            judge_rubric="Evaluate analysis",
        )
        assert needs_sample_input(spec) is True

    def test_no_reference_no_need(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import needs_sample_input

        spec = AgentTaskSpec(
            task_prompt="Write a haiku about nature.",
            judge_rubric="Evaluate creativity",
        )
        assert needs_sample_input(spec) is False

    def test_already_has_sample_input(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import needs_sample_input

        spec = AgentTaskSpec(
            task_prompt="Analyze the following data set.",
            judge_rubric="Evaluate analysis",
            sample_input='{"customers": []}',
        )
        assert needs_sample_input(spec) is False

    def test_inline_data_no_need(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import needs_sample_input

        spec = AgentTaskSpec(
            task_prompt=(
                "Analyze the following patient profile:\n\n"
                "Name: John Smith\nAge: 45\nMedications: Warfarin, Aspirin\n\n"
                "Identify drug interactions."
            ),
            judge_rubric="Evaluate analysis",
        )
        assert needs_sample_input(spec) is False

    def test_using_the_provided_with_inline_data_no_need(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import needs_sample_input

        spec = AgentTaskSpec(
            task_prompt=(
                "Using the provided timeline below:\n\n"
                "Time: 12:00\n"
                "Event: Disk reached 100% utilization\n\n"
                "Summarize the operational impact."
            ),
            judge_rubric="Evaluate analysis",
        )
        assert needs_sample_input(spec) is False

    def test_long_plain_prose_still_needs_heal(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import needs_sample_input

        spec = AgentTaskSpec(
            task_prompt=(
                "Analyze the following customer complaint and explain the refund exposure, escalation path, and contractual risk."
            ),
            judge_rubric="Evaluate analysis",
        )
        assert needs_sample_input(spec) is True


# ===========================================================================
# generate_synthetic_sample_input
# ===========================================================================


class TestGenerateSyntheticSampleInput:
    def test_generates_from_description(self) -> None:
        from autocontext.scenarios.custom.spec_auto_heal import (
            generate_synthetic_sample_input,
        )

        sample = generate_synthetic_sample_input(
            task_prompt="Analyze the following drug interaction pairs for safety risks.",
            description="Create a drug interaction prediction task",
        )
        assert len(sample) > 0
        assert "sample" in sample.lower() or "{" in sample

    def test_generates_without_description(self) -> None:
        from autocontext.scenarios.custom.spec_auto_heal import (
            generate_synthetic_sample_input,
        )

        sample = generate_synthetic_sample_input(
            task_prompt="You will be provided with customer data. Summarize key metrics.",
        )
        assert len(sample) > 0

    def test_generates_json_shaped_input(self) -> None:
        from autocontext.scenarios.custom.spec_auto_heal import (
            generate_synthetic_sample_input,
        )

        sample = generate_synthetic_sample_input(
            task_prompt="Given the following data, classify the items.",
        )
        # Should produce some structured placeholder
        assert len(sample) > 10


# ===========================================================================
# heal_spec_sample_input — auto-heal the spec
# ===========================================================================


class TestHealSpecSampleInput:
    def test_heals_missing_sample_input(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import heal_spec_sample_input

        spec = AgentTaskSpec(
            task_prompt="You will be provided with customer data. Analyze it.",
            judge_rubric="Evaluate analysis",
        )
        healed = heal_spec_sample_input(spec, description="Analyze customer data")
        assert healed.sample_input is not None
        assert len(healed.sample_input) > 0

    def test_drops_unreachable_runtime_context_requirements(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import heal_spec_runtime_context_requirements

        spec = AgentTaskSpec(
            task_prompt="Assess a medication interaction case.",
            judge_rubric="Evaluate accuracy.",
            sample_input='{"case_id": "poly_07"}',
            context_preparation="Load patient_case, judge_ground_truth_interactions, and prior_playbook_patterns.",
            required_context_keys=[
                "patient_case",
                "judge_ground_truth_interactions",
                "prior_playbook_patterns",
            ],
        )

        healed = heal_spec_runtime_context_requirements(spec)

        assert healed.context_preparation is None
        assert healed.required_context_keys is None

    def test_preserves_runtime_supported_context_requirements(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import heal_spec_runtime_context_requirements

        spec = AgentTaskSpec(
            task_prompt="Summarize the reference document.",
            judge_rubric="Evaluate faithfulness.",
            context_preparation="Load the reference document into state.",
            reference_context="Reference facts.",
            required_context_keys=["reference_context"],
        )

        healed = heal_spec_runtime_context_requirements(spec)

        assert healed.context_preparation == "Load the reference document into state."
        assert healed.required_context_keys == ["reference_context"]

    def test_does_not_overwrite_existing(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import heal_spec_sample_input

        spec = AgentTaskSpec(
            task_prompt="Analyze the following data.",
            judge_rubric="Evaluate",
            sample_input='{"existing": true}',
        )
        healed = heal_spec_sample_input(spec, description="data task")
        assert healed.sample_input == '{"existing": true}'

    def test_does_not_modify_non_data_tasks(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import heal_spec_sample_input

        spec = AgentTaskSpec(
            task_prompt="Write a persuasive essay about climate change.",
            judge_rubric="Evaluate persuasiveness",
        )
        healed = heal_spec_sample_input(spec, description="essay task")
        assert healed.sample_input is None

    def test_does_not_modify_inline_data_task(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.spec_auto_heal import heal_spec_sample_input

        spec = AgentTaskSpec(
            task_prompt=(
                "Using the provided timeline below:\n\n"
                "Time: 12:00\n"
                "Event: Disk reached 100% utilization\n\n"
                "Summarize the operational impact."
            ),
            judge_rubric="Evaluate analysis",
        )
        healed = heal_spec_sample_input(spec, description="incident analysis")
        assert healed.sample_input is None

    def test_healed_spec_passes_validation(self) -> None:
        """After healing, the spec should pass validate_spec without data-reference errors."""
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec
        from autocontext.scenarios.custom.spec_auto_heal import heal_spec_sample_input

        spec = AgentTaskSpec(
            task_prompt="You will be provided with patient records. Analyze drug interactions.",
            judge_rubric="Evaluate completeness and accuracy",
        )
        # Before healing: should fail validation
        errors_before = validate_spec(spec)
        assert any("sample_input" in e for e in errors_before)

        # After healing: should pass
        healed = heal_spec_sample_input(spec, description="drug interaction analysis")
        errors_after = validate_spec(healed)
        assert not any("sample_input" in e for e in errors_after)
