"""Tests for rubric coherence pre-check utility."""

from __future__ import annotations

from autocontext.execution.rubric_coherence import check_rubric_coherence


def test_detects_contradictory_adjective_pairs() -> None:
    result = check_rubric_coherence("Write a simple yet complex analysis of the topic")
    assert not result.is_coherent
    assert len(result.warnings) >= 1
    assert any("simple" in w and "complex" in w for w in result.warnings)


def test_detects_vague_rubric_with_generic_terms() -> None:
    result = check_rubric_coherence(
        "Evaluate good quality and appropriate content with nice output and proper formatting"
    )
    assert not result.is_coherent
    assert any("vague" in w for w in result.warnings)


def test_detects_underspecified_short_rubric() -> None:
    result = check_rubric_coherence("Score the output")
    assert not result.is_coherent
    assert any("underspecified" in w for w in result.warnings)


def test_clean_rubric_passes() -> None:
    result = check_rubric_coherence(
        "Evaluate factual accuracy against provided references. "
        "Check code correctness by verifying all test cases pass. "
        "Assess clarity of explanation on a 0-1 scale."
    )
    assert result.is_coherent
    assert result.warnings == []


def test_multiple_warnings_accumulate() -> None:
    result = check_rubric_coherence(
        "Be brief and comprehensive with good nice appropriate adequate proper quality output"
    )
    assert not result.is_coherent
    # Should have at least: contradictory (brief/comprehensive) + vague terms
    assert len(result.warnings) >= 2


def test_detects_multiple_contradictory_pairs() -> None:
    result = check_rubric_coherence(
        "Write a simple and complex analysis that is both concise and detailed in its approach"
    )
    assert not result.is_coherent
    contradiction_warnings = [w for w in result.warnings if "contradictory" in w]
    assert len(contradiction_warnings) >= 2
