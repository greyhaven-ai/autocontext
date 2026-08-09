"""Equivalence tests: output_parser functions match existing inline parsing."""

from __future__ import annotations

from autocontext.harness.core.output_parser import extract_delimited_section, extract_json


class TestCoachParsingEquivalence:
    def test_playbook_extraction_matches(self) -> None:
        text = (
            "Some preamble\n"
            "<!-- PLAYBOOK_START -->\n"
            "Strategy: balanced offense\n"
            "<!-- PLAYBOOK_END -->\n"
            "<!-- LESSONS_START -->\n"
            "- Lesson 1\n"
            "<!-- LESSONS_END -->\n"
        )
        playbook = extract_delimited_section(text, "<!-- PLAYBOOK_START -->", "<!-- PLAYBOOK_END -->")
        lessons = extract_delimited_section(text, "<!-- LESSONS_START -->", "<!-- LESSONS_END -->")
        assert playbook == "Strategy: balanced offense"
        assert lessons == "- Lesson 1"

    def test_hints_extraction(self) -> None:
        text = "Coach output\n<!-- COMPETITOR_HINTS_START -->\nTry higher aggression\n<!-- COMPETITOR_HINTS_END -->\n"
        hints = extract_delimited_section(text, "<!-- COMPETITOR_HINTS_START -->", "<!-- COMPETITOR_HINTS_END -->")
        assert hints == "Try higher aggression"

    def test_missing_section_returns_none(self) -> None:
        text = "No markers here"
        assert extract_delimited_section(text, "<!-- PLAYBOOK_START -->", "<!-- PLAYBOOK_END -->") is None


class TestTranslatorParsingEquivalence:
    # These three shapes used to be pinned against strip_json_fences, which is
    # what the translator called before AC-910 Task 4 migrated it. They are
    # pinned against extract_json now so they still cover the parser this site
    # actually runs.
    def test_fenced_with_json_tag(self) -> None:
        text = '```json\n{"aggression": 0.8}\n```'
        assert extract_json(text) == {"aggression": 0.8}

    def test_fenced_without_tag(self) -> None:
        text = '```\n{"aggression": 0.8}\n```'
        assert extract_json(text) == {"aggression": 0.8}

    def test_unfenced_passthrough(self) -> None:
        text = '{"aggression": 0.8}'
        assert extract_json(text) == {"aggression": 0.8}

    def test_extract_json_full_pipeline(self) -> None:
        text = 'Here is the strategy:\n```json\n{"aggression": 0.8, "defense": 0.3}\n```'
        result = extract_json(text)
        assert result == {"aggression": 0.8, "defense": 0.3}
