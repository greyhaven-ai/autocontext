"""Tests for competitor refinement prompt (MTS-79)."""

from __future__ import annotations

import json

from mts.loop.refinement_prompt import build_refinement_prompt


def _build(**kwargs):  # type: ignore[no-untyped-def]
    return build_refinement_prompt(**kwargs)


class TestBuildRefinementPrompt:
    def test_includes_parent_strategy(self) -> None:
        strategy = json.dumps({"flag_x": 3, "flag_y": 4})
        prompt = _build(
            scenario_rules="Grid CTF rules",
            strategy_interface="flag_x: int, flag_y: int",
            evaluation_criteria="maximize score",
            parent_strategy=strategy,
            match_feedback="Lost 3/5 matches",
        )
        assert "<strategy>" in prompt
        assert strategy in prompt

    def test_includes_match_feedback(self) -> None:
        prompt = _build(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            parent_strategy="{}",
            match_feedback="Score: 0.3, errors: illegal move at turn 5",
        )
        assert "<match_feedback>" in prompt
        assert "illegal move at turn 5" in prompt

    def test_includes_scenario_context(self) -> None:
        prompt = _build(
            scenario_rules="Grid CTF: capture the flag",
            strategy_interface="flag_x: int",
            evaluation_criteria="maximize wins",
            parent_strategy="{}",
            match_feedback="feedback",
        )
        assert "Grid CTF: capture the flag" in prompt
        assert "flag_x: int" in prompt
        assert "maximize wins" in prompt

    def test_refinement_not_creation(self) -> None:
        prompt = _build(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            parent_strategy="{}",
            match_feedback="feedback",
        )
        assert "STRATEGY REFINEMENT" in prompt
        assert "not creating one from scratch" in prompt
        assert "Keep what works" in prompt

    def test_optional_playbook_included(self) -> None:
        prompt = _build(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            parent_strategy="{}",
            match_feedback="feedback",
            current_playbook="Use balanced approach",
        )
        assert "Use balanced approach" in prompt

    def test_optional_playbook_omitted_when_empty(self) -> None:
        prompt = _build(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            parent_strategy="{}",
            match_feedback="feedback",
            current_playbook="",
        )
        assert "Current playbook:" not in prompt

    def test_optional_trajectory_included(self) -> None:
        prompt = _build(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            parent_strategy="{}",
            match_feedback="feedback",
            score_trajectory="Gen1: 0.3, Gen2: 0.5",
        )
        assert "Gen1: 0.3, Gen2: 0.5" in prompt

    def test_optional_lessons_included(self) -> None:
        prompt = _build(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            parent_strategy="{}",
            match_feedback="feedback",
            operational_lessons="- High aggression works in early game",
        )
        assert "High aggression works in early game" in prompt

    def test_works_with_code_strategy(self) -> None:
        code_strategy = "if state['turn'] < 5:\n    result = {'flag_x': 3}\nelse:\n    result = {'flag_x': 7}"
        prompt = _build(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            parent_strategy=code_strategy,
            match_feedback="Score: 0.6",
        )
        assert "if state['turn'] < 5:" in prompt
        assert "result = {'flag_x': 3}" in prompt
