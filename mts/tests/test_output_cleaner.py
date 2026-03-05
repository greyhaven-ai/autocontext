"""Tests for the revision output cleaner."""

from __future__ import annotations

from mts.execution.output_cleaner import clean_revision_output


def test_strips_revised_output_header_and_analysis() -> None:
    text = "## Revised Output\n\nHello world\n\n**Analysis:**\n- Good stuff"
    assert clean_revision_output(text) == "Hello world"


def test_strips_key_changes_section() -> None:
    text = "The actual content here.\n\n## Key Changes Made\n- Changed X"
    assert clean_revision_output(text) == "The actual content here."


def test_strips_analysis_block() -> None:
    text = "My haiku here\n\n**Analysis:**\n- Syllable count: 5-7-5"
    assert clean_revision_output(text) == "My haiku here"


def test_passthrough_clean_content() -> None:
    text = "Just clean content\nNo metadata"
    assert clean_revision_output(text) == "Just clean content\nNo metadata"


def test_combined_header_analysis_key_changes() -> None:
    text = (
        "## Revised Output\n\nGood content\n\n"
        "**Analysis:**\n- Note\n\n## Key Changes Made\n- Change"
    )
    assert clean_revision_output(text) == "Good content"


def test_strips_analysis_section() -> None:
    text = "Content here\n\n## Analysis\nSome analysis text"
    assert clean_revision_output(text) == "Content here"


def test_strips_changes_section() -> None:
    text = "Content here\n\n## Changes\n- Item 1\n- Item 2"
    assert clean_revision_output(text) == "Content here"


def test_strips_improvements_section() -> None:
    text = "Content here\n\n## Improvements\n1. Better flow"
    assert clean_revision_output(text) == "Content here"


def test_strips_self_assessment_section() -> None:
    text = "Content here\n\n## Self-Assessment\nI improved X"
    assert clean_revision_output(text) == "Content here"


def test_strips_trailing_transforms_paragraph() -> None:
    text = "The revised content\n\nThis revision transforms the original by adding detail."
    assert clean_revision_output(text) == "The revised content"


def test_strips_trailing_improves_paragraph() -> None:
    text = "The revised content\n\nThis revision improves clarity and flow."
    assert clean_revision_output(text) == "The revised content"


def test_strips_trailing_addresses_paragraph() -> None:
    text = "The revised content\n\nThis revision addresses all feedback points."
    assert clean_revision_output(text) == "The revised content"


def test_strips_trailing_fixes_paragraph() -> None:
    text = "The revised content\n\nThis revision fixes the structural issues noted."
    assert clean_revision_output(text) == "The revised content"


def test_metadata_only_returns_empty() -> None:
    text = "## Revised Output\n\n## Key Changes Made\n- Change 1"
    assert clean_revision_output(text) == ""


def test_no_trailing_newline() -> None:
    text = "Clean content"
    assert clean_revision_output(text) == "Clean content"
