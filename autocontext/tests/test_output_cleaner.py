"""Tests for the revision output cleaner."""

from __future__ import annotations

from autocontext.execution.output_cleaner import clean_revision_output


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
    text = "## Revised Output\n\nGood content\n\n**Analysis:**\n- Note\n\n## Key Changes Made\n- Change"
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


# -- AC-754: strip markdown code fences before verifier sees output --


def test_strips_lang_tagged_code_fence_wrapper() -> None:
    # The common case: claude-cli returns lean wrapped in ```lean ... ```.
    text = "```lean\ntheorem foo : 1 = 1 := rfl\n```"
    assert clean_revision_output(text) == "theorem foo : 1 = 1 := rfl"


def test_strips_bare_code_fence_wrapper() -> None:
    # Some prompts elicit a ``` ... ``` block without a language tag.
    text = "```\nx = 1\nprint(x)\n```"
    assert clean_revision_output(text) == "x = 1\nprint(x)"


def test_strips_fence_wrapper_with_surrounding_whitespace() -> None:
    # Leading / trailing whitespace around the fence wrapper is common.
    text = "\n  ```python\nprint('ok')\n```  \n"
    assert clean_revision_output(text) == "print('ok')"


def test_passthrough_when_no_outer_fence() -> None:
    # Inline ``` markers inside otherwise-unwrapped content must not be
    # touched. Only an outer wrapper is stripped.
    text = "Some text with `inline` and ``` not a fence opener inline."
    assert clean_revision_output(text) == text


def test_passthrough_when_only_opening_fence() -> None:
    # Unbalanced fences are not a "wrapper" and must be preserved (better to
    # let the verifier complain than silently mangle non-fence content).
    text = "```lean\ntheorem foo : 1 = 1 := rfl"
    assert clean_revision_output(text) == "```lean\ntheorem foo : 1 = 1 := rfl"


def test_passthrough_when_only_closing_fence() -> None:
    text = "theorem foo : 1 = 1 := rfl\n```"
    assert clean_revision_output(text) == "theorem foo : 1 = 1 := rfl\n```"


def test_preserves_inner_fences_inside_outer_wrapper() -> None:
    # If the wrapper holds a doc-string with nested fence markers, only the
    # outer wrapper is stripped; inner markers stay intact.
    text = "```markdown\nExample block:\n```python\nx = 1\n```\nEnd.\n```"
    expected = "Example block:\n```python\nx = 1\n```\nEnd."
    assert clean_revision_output(text) == expected


def test_fence_strip_runs_after_metadata_strip() -> None:
    # When both metadata and fences are present, the cleaner removes both
    # in a single pass and returns just the code.
    text = "## Revised Output\n\n```lean\ntheorem foo : 1 = 1 := rfl\n```\n\n## Key Changes Made\n- did stuff"
    assert clean_revision_output(text) == "theorem foo : 1 = 1 := rfl"
