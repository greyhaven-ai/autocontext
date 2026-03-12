"""Tests for playbook integrity guard."""
from __future__ import annotations

from autocontext.knowledge.playbook_guard import PlaybookGuard


def test_accepts_good_update() -> None:
    current = "## Strategy\n- Use aggression 0.5\n\n## Lessons\n- Lesson 1\n- Lesson 2"
    proposed = "## Strategy\n- Use aggression 0.6\n\n## Lessons\n- Lesson 1\n- Lesson 2\n- Lesson 3"
    guard = PlaybookGuard()
    result = guard.check(current, proposed)
    assert result.approved


def test_rejects_massive_shrink() -> None:
    current = "## Strategy\n" + "- Important point\n" * 20
    proposed = "## Strategy\n- Simplified."
    guard = PlaybookGuard(max_shrink_ratio=0.3)
    result = guard.check(current, proposed)
    assert not result.approved
    assert "shrink" in result.reason.lower()


def test_rejects_missing_markers() -> None:
    current = "<!-- PLAYBOOK_START -->\nContent\n<!-- PLAYBOOK_END -->"
    proposed = "Content without markers"
    guard = PlaybookGuard()
    result = guard.check(current, proposed)
    assert not result.approved


def test_accepts_when_no_markers_in_current() -> None:
    current = "Simple playbook without markers"
    proposed = "Updated simple playbook"
    guard = PlaybookGuard()
    result = guard.check(current, proposed)
    assert result.approved


def test_accepts_when_markers_preserved() -> None:
    current = "<!-- PLAYBOOK_START -->\nOld\n<!-- PLAYBOOK_END -->"
    proposed = "<!-- PLAYBOOK_START -->\nNew and improved\n<!-- PLAYBOOK_END -->"
    guard = PlaybookGuard()
    result = guard.check(current, proposed)
    assert result.approved


def test_empty_current_accepts_anything() -> None:
    guard = PlaybookGuard()
    result = guard.check("", "New playbook content")
    assert result.approved


def test_rejects_empty_proposed_when_current_non_empty() -> None:
    current = "## Strategy\n- Important content"
    guard = PlaybookGuard()
    result = guard.check(current, "")
    assert not result.approved
    assert "empty" in result.reason.lower()


def test_rejects_missing_end_marker() -> None:
    current = "<!-- PLAYBOOK_START -->\nContent\n<!-- PLAYBOOK_END -->"
    proposed = "<!-- PLAYBOOK_START -->\nContent without end marker"
    guard = PlaybookGuard()
    result = guard.check(current, proposed)
    assert not result.approved
    assert "PLAYBOOK_END" in result.reason


def test_checks_lessons_markers() -> None:
    current = "<!-- LESSONS_START -->\n- Lesson 1\n<!-- LESSONS_END -->"
    proposed = "No lessons markers here"
    guard = PlaybookGuard()
    result = guard.check(current, proposed)
    assert not result.approved
    assert "LESSONS_START" in result.reason


def test_checks_competitor_hints_markers() -> None:
    current = "<!-- COMPETITOR_HINTS_START -->\nHints\n<!-- COMPETITOR_HINTS_END -->"
    proposed = "Updated content without hints markers, long enough to pass shrinkage check easily"
    guard = PlaybookGuard()
    result = guard.check(current, proposed)
    assert not result.approved
    assert "COMPETITOR_HINTS_START" in result.reason
