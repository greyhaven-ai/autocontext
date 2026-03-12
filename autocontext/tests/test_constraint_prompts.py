"""Tests for constraint-oriented prompt reframing (PR 2)."""

from __future__ import annotations

from unittest.mock import MagicMock

from autocontext.agents.curator import (
    _CURATOR_ASSESSMENT_CONSTRAINT,
    _CURATOR_CONSOLIDATION_CONSTRAINT,
    KnowledgeCurator,
)
from autocontext.config.settings import AppSettings
from autocontext.prompts.templates import (
    _ANALYST_CONSTRAINT_SUFFIX,
    _ARCHITECT_CONSTRAINT_SUFFIX,
    _COACH_CONSTRAINT_SUFFIX,
    _COMPETITOR_CONSTRAINT_SUFFIX,
    build_prompt_bundle,
)
from autocontext.rlm.prompts import (
    _RLM_ANALYST_CONSTRAINT,
    _RLM_ARCHITECT_CONSTRAINT,
    ANALYST_MONTY_RLM_SYSTEM,
    ANALYST_MONTY_RLM_SYSTEM_CONSTRAINED,
    ANALYST_RLM_SYSTEM,
    ANALYST_RLM_SYSTEM_CONSTRAINED,
    ARCHITECT_MONTY_RLM_SYSTEM,
    ARCHITECT_MONTY_RLM_SYSTEM_CONSTRAINED,
    ARCHITECT_RLM_SYSTEM,
    ARCHITECT_RLM_SYSTEM_CONSTRAINED,
)
from autocontext.scenarios.base import Observation


def _make_observation() -> Observation:
    return Observation(
        narrative="test narrative",
        state={"key": "value"},
        constraints=["test constraint"],
    )


def _make_bundle(constraint_mode: bool = False) -> object:
    return build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=_make_observation(),
        current_playbook="playbook",
        available_tools="tools",
        constraint_mode=constraint_mode,
    )


# --- Constraint suffix constants ---


class TestConstraintSuffixConstants:
    def test_competitor_constraint_contains_do_not(self) -> None:
        assert "Do NOT" in _COMPETITOR_CONSTRAINT_SUFFIX

    def test_analyst_constraint_contains_do_not(self) -> None:
        assert "Do NOT" in _ANALYST_CONSTRAINT_SUFFIX

    def test_coach_constraint_contains_do_not(self) -> None:
        assert "Do NOT" in _COACH_CONSTRAINT_SUFFIX

    def test_architect_constraint_contains_do_not(self) -> None:
        assert "Do NOT" in _ARCHITECT_CONSTRAINT_SUFFIX


# --- build_prompt_bundle with constraint_mode ---


class TestBuildPromptBundleConstraints:
    def test_constraint_mode_true_injects_competitor_constraint(self) -> None:
        bundle = _make_bundle(constraint_mode=True)
        assert "Do NOT repeat any strategy from the registry" in bundle.competitor

    def test_constraint_mode_true_injects_analyst_constraint(self) -> None:
        bundle = _make_bundle(constraint_mode=True)
        assert "Do NOT report findings without supporting evidence" in bundle.analyst

    def test_constraint_mode_true_injects_coach_constraint(self) -> None:
        bundle = _make_bundle(constraint_mode=True)
        assert "Do NOT remove working strategies" in bundle.coach

    def test_constraint_mode_true_injects_architect_constraint(self) -> None:
        bundle = _make_bundle(constraint_mode=True)
        assert "Do NOT propose tools that duplicate" in bundle.architect

    def test_constraint_mode_false_no_constraint_text(self) -> None:
        bundle = _make_bundle(constraint_mode=False)
        assert "Do NOT repeat any strategy from the registry" not in bundle.competitor
        assert "Do NOT report findings without supporting evidence" not in bundle.analyst
        assert "Do NOT remove working strategies" not in bundle.coach
        assert "Do NOT propose tools that duplicate" not in bundle.architect

    def test_constraint_mode_default_is_false(self) -> None:
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_make_observation(),
            current_playbook="playbook",
            available_tools="tools",
        )
        assert "Do NOT repeat any strategy from the registry" not in bundle.competitor

    def test_coach_constraint_preserves_structural_markers(self) -> None:
        bundle = _make_bundle(constraint_mode=True)
        assert "<!-- PLAYBOOK_START -->" in bundle.coach
        assert "<!-- PLAYBOOK_END -->" in bundle.coach
        assert "<!-- LESSONS_START -->" in bundle.coach
        assert "<!-- LESSONS_END -->" in bundle.coach
        assert "<!-- COMPETITOR_HINTS_START -->" in bundle.coach
        assert "<!-- COMPETITOR_HINTS_END -->" in bundle.coach

    def test_constraint_before_role_instruction(self) -> None:
        bundle = _make_bundle(constraint_mode=True)
        # Competitor: constraint should appear before "Describe your strategy"
        constraint_pos = bundle.competitor.find("Do NOT repeat any strategy")
        instruction_pos = bundle.competitor.find("Describe your strategy reasoning")
        assert constraint_pos < instruction_pos
        # Analyst: constraint before "Analyze strengths"
        constraint_pos = bundle.analyst.find("Do NOT report findings")
        instruction_pos = bundle.analyst.find("Analyze strengths/failures")
        assert constraint_pos < instruction_pos

    def test_constraint_mode_false_matches_no_arg(self) -> None:
        bundle_default = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_make_observation(),
            current_playbook="playbook",
            available_tools="tools",
        )
        bundle_explicit = _make_bundle(constraint_mode=False)
        assert bundle_default.competitor == bundle_explicit.competitor
        assert bundle_default.analyst == bundle_explicit.analyst
        assert bundle_default.coach == bundle_explicit.coach
        assert bundle_default.architect == bundle_explicit.architect


# --- RLM constrained variants ---


class TestRlmConstrainedPrompts:
    def test_rlm_analyst_constrained_contains_constraint(self) -> None:
        assert "Do NOT report findings without supporting evidence" in ANALYST_RLM_SYSTEM_CONSTRAINED

    def test_rlm_architect_constrained_contains_constraint(self) -> None:
        assert "Do NOT propose tools that duplicate" in ARCHITECT_RLM_SYSTEM_CONSTRAINED

    def test_monty_analyst_constrained_contains_constraint(self) -> None:
        assert "Do NOT report findings without supporting evidence" in ANALYST_MONTY_RLM_SYSTEM_CONSTRAINED

    def test_monty_architect_constrained_contains_constraint(self) -> None:
        assert "Do NOT propose tools that duplicate" in ARCHITECT_MONTY_RLM_SYSTEM_CONSTRAINED

    def test_unconstrained_does_not_have_constraint(self) -> None:
        assert "Do NOT report findings without supporting evidence" not in ANALYST_RLM_SYSTEM
        assert "Do NOT propose tools that duplicate" not in ARCHITECT_RLM_SYSTEM
        assert "Do NOT report findings without supporting evidence" not in ANALYST_MONTY_RLM_SYSTEM
        assert "Do NOT propose tools that duplicate" not in ARCHITECT_MONTY_RLM_SYSTEM

    def test_constrained_still_has_important_rules(self) -> None:
        assert "## Important rules" in ANALYST_RLM_SYSTEM_CONSTRAINED
        assert "## Important rules" in ARCHITECT_RLM_SYSTEM_CONSTRAINED

    def test_constraint_before_important_rules(self) -> None:
        constraint_pos = ANALYST_RLM_SYSTEM_CONSTRAINED.find("## Constraints")
        rules_pos = ANALYST_RLM_SYSTEM_CONSTRAINED.find("## Important rules")
        assert constraint_pos < rules_pos

    def test_rlm_constraint_constants(self) -> None:
        assert "Do NOT" in _RLM_ANALYST_CONSTRAINT
        assert "Do NOT" in _RLM_ARCHITECT_CONSTRAINT


# --- Curator constraint_mode ---


class TestCuratorConstraints:
    def test_assess_playbook_quality_with_constraints(self) -> None:
        runtime = MagicMock()
        exec_result = MagicMock()
        exec_result.content = "<!-- CURATOR_DECISION: accept -->\n<!-- CURATOR_SCORE: 8 -->"
        runtime.run_task.return_value = exec_result

        curator = KnowledgeCurator(runtime, "test-model")
        decision, _ = curator.assess_playbook_quality(
            current_playbook="current",
            proposed_playbook="proposed",
            score_trajectory="trajectory",
            recent_analysis="analysis",
            constraint_mode=True,
        )

        call_args = runtime.run_task.call_args[0][0]
        assert "Do NOT accept a playbook that removes validated" in call_args.prompt

    def test_assess_playbook_quality_without_constraints(self) -> None:
        runtime = MagicMock()
        exec_result = MagicMock()
        exec_result.content = "<!-- CURATOR_DECISION: accept -->\n<!-- CURATOR_SCORE: 8 -->"
        runtime.run_task.return_value = exec_result

        curator = KnowledgeCurator(runtime, "test-model")
        decision, _ = curator.assess_playbook_quality(
            current_playbook="current",
            proposed_playbook="proposed",
            score_trajectory="trajectory",
            recent_analysis="analysis",
            constraint_mode=False,
        )

        call_args = runtime.run_task.call_args[0][0]
        assert "Do NOT accept a playbook that removes validated" not in call_args.prompt

    def test_consolidate_lessons_with_constraints(self) -> None:
        runtime = MagicMock()
        exec_result = MagicMock()
        exec_result.content = (
            "<!-- CONSOLIDATED_LESSONS_START -->\n- lesson 1\n<!-- CONSOLIDATED_LESSONS_END -->\n"
            "<!-- LESSONS_REMOVED: 1 -->"
        )
        runtime.run_task.return_value = exec_result

        curator = KnowledgeCurator(runtime, "test-model")
        result, _ = curator.consolidate_lessons(
            existing_lessons=["- lesson 1", "- lesson 2"],
            max_lessons=1,
            score_trajectory="trajectory",
            constraint_mode=True,
        )

        call_args = runtime.run_task.call_args[0][0]
        assert "Do NOT remove lessons that are supported by score" in call_args.prompt

    def test_consolidate_lessons_without_constraints(self) -> None:
        runtime = MagicMock()
        exec_result = MagicMock()
        exec_result.content = (
            "<!-- CONSOLIDATED_LESSONS_START -->\n- lesson 1\n<!-- CONSOLIDATED_LESSONS_END -->\n"
            "<!-- LESSONS_REMOVED: 1 -->"
        )
        runtime.run_task.return_value = exec_result

        curator = KnowledgeCurator(runtime, "test-model")
        result, _ = curator.consolidate_lessons(
            existing_lessons=["- lesson 1", "- lesson 2"],
            max_lessons=1,
            score_trajectory="trajectory",
            constraint_mode=False,
        )

        call_args = runtime.run_task.call_args[0][0]
        assert "Do NOT remove lessons that are supported by score" not in call_args.prompt

    def test_curator_constraint_constants(self) -> None:
        assert "Do NOT" in _CURATOR_ASSESSMENT_CONSTRAINT
        assert "Do NOT" in _CURATOR_CONSOLIDATION_CONSTRAINT


# --- Settings ---


class TestConstraintSettings:
    def test_default_constraint_prompts_enabled(self) -> None:
        settings = AppSettings()
        assert settings.constraint_prompts_enabled is True

    def test_constraint_prompts_disabled(self) -> None:
        settings = AppSettings(constraint_prompts_enabled=False)
        assert settings.constraint_prompts_enabled is False
