"""Tests for AC-337: bidirectional competitor hint feedback after tournament.

Covers: HintFeedback, build_hint_reflection_prompt, parse_hint_feedback,
format_hint_feedback_for_coach.
"""

from __future__ import annotations

# ===========================================================================
# HintFeedback
# ===========================================================================


class TestHintFeedback:
    def test_construction(self) -> None:
        from autocontext.agents.hint_feedback import HintFeedback

        fb = HintFeedback(
            helpful=["edge control strategy", "defensive perimeter first"],
            misleading=["avoid corners — actually corners were key"],
            missing=["no guidance on mid-game transitions"],
            generation=5,
        )
        assert len(fb.helpful) == 2
        assert len(fb.misleading) == 1
        assert fb.generation == 5

    def test_is_empty(self) -> None:
        from autocontext.agents.hint_feedback import HintFeedback

        fb = HintFeedback(helpful=[], misleading=[], missing=[], generation=1)
        assert fb.is_empty() is True

        fb2 = HintFeedback(helpful=["x"], misleading=[], missing=[], generation=1)
        assert fb2.is_empty() is False

    def test_roundtrip(self) -> None:
        from autocontext.agents.hint_feedback import HintFeedback

        fb = HintFeedback(
            helpful=["a"], misleading=["b"], missing=["c"], generation=3,
        )
        d = fb.to_dict()
        restored = HintFeedback.from_dict(d)
        assert restored.helpful == ["a"]
        assert restored.misleading == ["b"]
        assert restored.generation == 3


# ===========================================================================
# build_hint_reflection_prompt
# ===========================================================================


class TestBuildHintReflectionPrompt:
    def test_includes_hints_and_scores(self) -> None:
        from autocontext.agents.hint_feedback import build_hint_reflection_prompt

        prompt = build_hint_reflection_prompt(
            hints="- Try high aggression\n- Focus on corners",
            tournament_best_score=0.75,
            tournament_mean_score=0.65,
            previous_best=0.60,
        )
        assert "high aggression" in prompt
        assert "corners" in prompt
        assert "0.75" in prompt

    def test_empty_hints(self) -> None:
        from autocontext.agents.hint_feedback import build_hint_reflection_prompt

        prompt = build_hint_reflection_prompt(
            hints="",
            tournament_best_score=0.5,
            tournament_mean_score=0.4,
            previous_best=0.3,
        )
        assert "no hints" in prompt.lower() or len(prompt) > 0


# ===========================================================================
# parse_hint_feedback
# ===========================================================================


class TestParseHintFeedback:
    def test_parses_json(self) -> None:
        import json

        from autocontext.agents.hint_feedback import parse_hint_feedback

        raw = json.dumps({
            "helpful": ["edge control"],
            "misleading": ["avoid center"],
            "missing": ["transition guidance"],
        })
        fb = parse_hint_feedback(raw, generation=4)
        assert fb.helpful == ["edge control"]
        assert fb.misleading == ["avoid center"]
        assert fb.missing == ["transition guidance"]
        assert fb.generation == 4

    def test_parses_json_in_fences(self) -> None:
        from autocontext.agents.hint_feedback import parse_hint_feedback

        raw = '```json\n{"helpful": ["a"], "misleading": [], "missing": []}\n```'
        fb = parse_hint_feedback(raw, generation=2)
        assert fb.helpful == ["a"]

    def test_handles_malformed_gracefully(self) -> None:
        from autocontext.agents.hint_feedback import parse_hint_feedback

        fb = parse_hint_feedback("This is not JSON at all.", generation=1)
        assert fb.is_empty()

    def test_handles_partial_fields(self) -> None:
        import json

        from autocontext.agents.hint_feedback import parse_hint_feedback

        raw = json.dumps({"helpful": ["x"]})
        fb = parse_hint_feedback(raw, generation=1)
        assert fb.helpful == ["x"]
        assert fb.misleading == []
        assert fb.missing == []

    def test_normalizes_string_fields_to_single_item_lists(self) -> None:
        import json

        from autocontext.agents.hint_feedback import parse_hint_feedback

        raw = json.dumps({
            "helpful": "focus on corners",
            "misleading": "avoid the center",
            "missing": "transition guidance",
        })
        fb = parse_hint_feedback(raw, generation=2)
        assert fb.helpful == ["focus on corners"]
        assert fb.misleading == ["avoid the center"]
        assert fb.missing == ["transition guidance"]

    def test_filters_non_string_entries_from_lists(self) -> None:
        import json

        from autocontext.agents.hint_feedback import parse_hint_feedback

        raw = json.dumps({
            "helpful": [" corners ", 12, "", None],
            "misleading": [False, "too passive"],
            "missing": [" late game plan ", {"x": 1}],
        })
        fb = parse_hint_feedback(raw, generation=3)
        assert fb.helpful == ["corners"]
        assert fb.misleading == ["too passive"]
        assert fb.missing == ["late game plan"]


# ===========================================================================
# format_hint_feedback_for_coach
# ===========================================================================


class TestFormatHintFeedbackForCoach:
    def test_formats_feedback(self) -> None:
        from autocontext.agents.hint_feedback import (
            HintFeedback,
            format_hint_feedback_for_coach,
        )

        fb = HintFeedback(
            helpful=["edge control"],
            misleading=["avoid corners — corners were actually key"],
            missing=["mid-game transition guidance"],
            generation=5,
        )
        text = format_hint_feedback_for_coach(fb)
        assert "edge control" in text
        assert "corners" in text
        assert "transition" in text
        assert "5" in text or "gen 5" in text.lower()

    def test_empty_feedback_returns_empty(self) -> None:
        from autocontext.agents.hint_feedback import (
            HintFeedback,
            format_hint_feedback_for_coach,
        )

        fb = HintFeedback(helpful=[], misleading=[], missing=[], generation=1)
        text = format_hint_feedback_for_coach(fb)
        assert text == ""

    def test_none_returns_empty(self) -> None:
        from autocontext.agents.hint_feedback import format_hint_feedback_for_coach

        assert format_hint_feedback_for_coach(None) == ""
