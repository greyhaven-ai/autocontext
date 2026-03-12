"""Tests for KnowledgeCurator agent."""
from __future__ import annotations

from autocontext.agents.curator import (
    parse_curator_lesson_result,
    parse_curator_playbook_decision,
)
from autocontext.agents.llm_client import DeterministicDevClient


def test_parse_playbook_accept() -> None:
    content = "Review done.\n<!-- CURATOR_DECISION: accept -->\n<!-- CURATOR_SCORE: 8 -->"
    result = parse_curator_playbook_decision(content)
    assert result.decision == "accept"
    assert result.score == 8


def test_parse_playbook_reject() -> None:
    content = "Review done.\n<!-- CURATOR_DECISION: reject -->\n<!-- CURATOR_SCORE: 3 -->"
    result = parse_curator_playbook_decision(content)
    assert result.decision == "reject"


def test_parse_playbook_merge() -> None:
    content = (
        "Merging both.\n"
        "<!-- CURATOR_DECISION: merge -->\n"
        "<!-- CURATOR_SCORE: 6 -->\n"
        "<!-- CURATOR_PLAYBOOK_START -->\nMerged playbook content.\n<!-- CURATOR_PLAYBOOK_END -->"
    )
    result = parse_curator_playbook_decision(content)
    assert result.decision == "merge"
    assert "Merged playbook content" in result.playbook


def test_parse_score() -> None:
    content = "<!-- CURATOR_DECISION: accept -->\n<!-- CURATOR_SCORE: 9 -->"
    result = parse_curator_playbook_decision(content)
    assert result.score == 9


def test_parse_lesson_consolidation() -> None:
    content = (
        "Consolidated:\n"
        "<!-- CONSOLIDATED_LESSONS_START -->\n"
        "- Lesson A\n"
        "- Lesson B\n"
        "- Lesson C\n"
        "<!-- CONSOLIDATED_LESSONS_END -->\n"
        "<!-- LESSONS_REMOVED: 5 -->"
    )
    result = parse_curator_lesson_result(content)
    assert len(result.consolidated_lessons) == 3
    assert result.removed_count == 5
    assert "Lesson A" in result.consolidated_lessons[0]


def test_curator_rejects_low_quality() -> None:
    content = "Too vague.\n<!-- CURATOR_DECISION: reject -->\n<!-- CURATOR_SCORE: 2 -->"
    result = parse_curator_playbook_decision(content)
    assert result.decision == "reject"
    assert result.score == 2


def test_curator_accepts_good_playbook() -> None:
    content = "Great detail.\n<!-- CURATOR_DECISION: accept -->\n<!-- CURATOR_SCORE: 9 -->"
    result = parse_curator_playbook_decision(content)
    assert result.decision == "accept"
    assert result.score == 9


def test_curator_merges() -> None:
    content = (
        "<!-- CURATOR_DECISION: merge -->\n"
        "<!-- CURATOR_SCORE: 7 -->\n"
        "<!-- CURATOR_PLAYBOOK_START -->\n"
        "## Combined\n- Best of both\n"
        "<!-- CURATOR_PLAYBOOK_END -->"
    )
    result = parse_curator_playbook_decision(content)
    assert result.decision == "merge"
    assert "Best of both" in result.playbook


def test_curator_disabled_skips() -> None:
    """When curator is None, nothing happens."""
    # Just testing that parse functions handle empty/missing markers gracefully
    result = parse_curator_playbook_decision("No markers here")
    assert result.decision == "accept"  # default fallback
    assert result.score == 5  # default


def test_deterministic_curator_branches() -> None:
    client = DeterministicDevClient()
    # Playbook quality
    resp = client.generate(
        model="test", prompt="You are a curator assessing playbook quality.", max_tokens=1000, temperature=0.3
    )
    assert "CURATOR_DECISION" in resp.text
    # Consolidation
    resp2 = client.generate(
        model="test", prompt="You are a curator consolidating lessons.", max_tokens=1000, temperature=0.3
    )
    assert "CONSOLIDATED_LESSONS_START" in resp2.text
