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
            helpful=["a"],
            misleading=["b"],
            missing=["c"],
            generation=3,
        )
        d = fb.to_dict()
        restored = HintFeedback.from_dict(d)
        assert restored.helpful == ["a"]
        assert restored.misleading == ["b"]
        assert restored.generation == 3

    def test_from_dict_normalizes_legacy_payload(self) -> None:
        from autocontext.agents.hint_feedback import HintFeedback

        restored = HintFeedback.from_dict(
            {
                "helpful": "focus on corners",
                "generation": 2,
            }
        )

        assert restored.helpful == ["focus on corners"]
        assert restored.misleading == []
        assert restored.missing == []
        assert restored.generation == 2

    def test_artifact_store_reads_legacy_feedback_payload(self, tmp_path) -> None:
        import json

        from autocontext.storage.artifacts import ArtifactStore

        runs = tmp_path / "runs"
        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        claude_skills = tmp_path / "claude_skills"
        store = ArtifactStore(runs, knowledge, skills, claude_skills)

        path = knowledge / "demo" / "hint_feedback" / "gen_1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"helpful": "use BFS", "generation": 1}),
            encoding="utf-8",
        )

        restored = store.read_latest_hint_feedback("demo", current_gen=2)

        assert restored is not None
        assert restored.helpful == ["use BFS"]
        assert restored.misleading == []
        assert restored.missing == []


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
        assert "Try high aggression" in prompt
        assert "Focus on corners" in prompt
        assert "0.75" in prompt
        assert "helpful_hint_numbers" in prompt

    def test_compacts_and_numbers_hint_items(self) -> None:
        from autocontext.agents.hint_feedback import (
            build_hint_reflection_prompt,
            prepare_hint_reflection_items,
        )

        hints = "\n".join(
            [
                "- **Do not** combine defense and routing changes.",
                "- Preserve one Soldier or Commander as the home anchor while Scouts handle lane pressure.",
                "- Immediate next run: redeploy the live **moderate** control baseline unchanged.",
                "- Keep path_bias between 0.50-0.60 for stability.",
                "- Promote only a probe that beats the scored moderate control on capture progress.",
                "- Try aggression=0.60 with defense=0.55 for balanced scoring.",
            ]
        )

        items = prepare_hint_reflection_items(hints)
        prompt = build_hint_reflection_prompt(
            hints=hints,
            tournament_best_score=0.75,
            tournament_mean_score=0.65,
            previous_best=0.60,
            hint_items=items,
        )

        assert len(items) < 6
        assert all("**" not in item for item in items)
        assert "1. Do not combine defense and routing changes." in prompt
        assert "helpful_hint_numbers" in prompt
        assert "Try aggression=0.60 with defense=0.55 for balanced scoring." not in prompt

    def test_preserves_identifier_underscores_during_hint_sanitization(self) -> None:
        from autocontext.agents.hint_feedback import prepare_hint_reflection_items

        items = prepare_hint_reflection_items(
            "- Keep `path_bias` between 0.50 and 0.60 for stability."
        )

        assert items == ["Keep path_bias between 0.50 and 0.60 for stability."]

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

        raw = json.dumps(
            {
                "helpful": ["edge control"],
                "misleading": ["avoid center"],
                "missing": ["transition guidance"],
            }
        )
        fb = parse_hint_feedback(raw, generation=4)
        assert fb.helpful == ["edge control"]
        assert fb.misleading == ["avoid center"]
        assert fb.missing == ["transition guidance"]
        assert fb.generation == 4

    def test_maps_hint_numbers_back_to_compacted_hint_items(self) -> None:
        import json

        from autocontext.agents.hint_feedback import parse_hint_feedback

        raw = json.dumps(
            {
                "helpful_hint_numbers": [1, 3],
                "misleading_hint_numbers": [2],
                "missing": ["late-game cleanup"],
            }
        )
        fb = parse_hint_feedback(
            raw,
            generation=4,
            hint_items=["keep anchor", "avoid over-commit", "reuse baseline"],
        )
        assert fb.helpful == ["keep anchor", "reuse baseline"]
        assert fb.misleading == ["avoid over-commit"]
        assert fb.missing == ["late-game cleanup"]

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

        raw = json.dumps(
            {
                "helpful": "focus on corners",
                "misleading": "avoid the center",
                "missing": "transition guidance",
            }
        )
        fb = parse_hint_feedback(raw, generation=2)
        assert fb.helpful == ["focus on corners"]
        assert fb.misleading == ["avoid the center"]
        assert fb.missing == ["transition guidance"]

    def test_filters_non_string_entries_from_lists(self) -> None:
        import json

        from autocontext.agents.hint_feedback import parse_hint_feedback

        raw = json.dumps(
            {
                "helpful": [" corners ", 12, "", None],
                "misleading": [False, "too passive"],
                "missing": [" late game plan ", {"x": 1}],
            }
        )
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
