"""Tests for Gap 6: Coach competitor hints section."""
from __future__ import annotations

from autocontext.agents.coach import parse_coach_sections
from autocontext.prompts.templates import build_prompt_bundle
from autocontext.scenarios.base import Observation


def test_coach_prompt_requests_hints() -> None:
    """Coach prompt contains COMPETITOR_HINTS markers."""
    prompts = build_prompt_bundle(
        scenario_rules="Test rules",
        strategy_interface='{"aggression": float}',
        evaluation_criteria="Win rate",
        previous_summary="best score: 0.0",
        observation=Observation(narrative="Test", state={}, constraints=[]),
        current_playbook="No playbook yet.",
        available_tools="No tools.",
    )
    assert "COMPETITOR_HINTS_START" in prompts.coach
    assert "COMPETITOR_HINTS_END" in prompts.coach


def test_parse_coach_hints() -> None:
    """parse_coach_sections() returns 3-tuple with hints."""
    content = (
        "<!-- PLAYBOOK_START -->\nPlaybook content\n<!-- PLAYBOOK_END -->\n\n"
        "<!-- LESSONS_START -->\n- Lesson 1\n<!-- LESSONS_END -->\n\n"
        "<!-- COMPETITOR_HINTS_START -->\n- Try aggression=0.65\n<!-- COMPETITOR_HINTS_END -->"
    )
    playbook, lessons, hints = parse_coach_sections(content)
    assert playbook == "Playbook content"
    assert "Lesson 1" in lessons
    assert "aggression=0.65" in hints


def test_hints_included_in_next_gen_competitor_prompt() -> None:
    """Gen 2 competitor prompt contains gen 1 hints when provided."""
    hints = "- Try aggression=0.65 with defense=0.50"
    prompts = build_prompt_bundle(
        scenario_rules="Test rules",
        strategy_interface='{"aggression": float}',
        evaluation_criteria="Win rate",
        previous_summary="best score: 0.5",
        observation=Observation(narrative="Test", state={}, constraints=[]),
        current_playbook="No playbook yet.",
        available_tools="No tools.",
        coach_competitor_hints=hints,
    )
    assert "Coach hints" in prompts.competitor or "coach hints" in prompts.competitor.lower()
    assert "aggression=0.65" in prompts.competitor


def test_missing_hints_defaults_empty() -> None:
    """No markers = empty string, backward compatible."""
    content = (
        "<!-- PLAYBOOK_START -->\nPlaybook\n<!-- PLAYBOOK_END -->\n\n"
        "<!-- LESSONS_START -->\n- Lesson\n<!-- LESSONS_END -->"
    )
    playbook, lessons, hints = parse_coach_sections(content)
    assert playbook == "Playbook"
    assert hints == ""
