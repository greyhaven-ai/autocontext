"""AC-932: a coach response that ignores the format must not fail silently.

The playbook is the loop's memory. Losing an update costs a generation's worth
of learning, and both engines used to lose it without saying so -- TypeScript by
dropping the update when any of six markers is missing, Python by accepting
whatever the model wrote as the playbook.

Measured on llama3.1:8b, 10 trials of the real coach instruction: 8 produced all
six markers, 2 produced none.
"""

from __future__ import annotations

import logging

import pytest


def test_unmarked_coach_output_becomes_the_playbook_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    """The fallback is not neutral: the model's prose becomes the playbook.

    Pinning the behavior AND the warning together, because the behavior is the
    reason the warning has to exist. Whatever the model wrote steers the next
    generation; silently is the defect, not the substitution itself.
    """
    from autocontext.agents.coach import parse_coach_sections

    prose = "Sure! Here's my advice:\n\nTry moving faster and avoid the guarded tiles."

    with caplog.at_level(logging.WARNING):
        playbook, lessons, hints = parse_coach_sections(prose)

    assert playbook == prose.strip()
    assert (lessons, hints) == ("", "")
    assert any("no playbook markers" in r.message for r in caplog.records), (
        "the whole response became the playbook with no warning"
    )


def test_well_formed_coach_output_warns_about_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The warning must not cry wolf on the normal path.

    A warning that fires on every generation is one operators learn to ignore,
    which would put us back where we started.
    """
    from autocontext.agents.coach import parse_coach_sections

    marked = (
        "<!-- PLAYBOOK_START -->\nP\n<!-- PLAYBOOK_END -->\n\n"
        "<!-- LESSONS_START -->\nL\n<!-- LESSONS_END -->\n\n"
        "<!-- COMPETITOR_HINTS_START -->\nH\n<!-- COMPETITOR_HINTS_END -->"
    )

    with caplog.at_level(logging.WARNING):
        playbook, lessons, hints = parse_coach_sections(marked)

    assert (playbook, lessons, hints) == ("P", "L", "H")
    assert not [r for r in caplog.records if "no playbook markers" in r.message]


def test_truncated_coach_output_still_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    """AC-904's guarantee must survive this change.

    START without END is truncation, and persisting the fragment would make a
    cut-off response the playbook. That path discards the update and warns; this
    asserts AC-932's new warning did not displace it.
    """
    from autocontext.agents.coach import parse_coach_sections

    truncated = "<!-- PLAYBOOK_START -->\nhalf a play"

    with caplog.at_level(logging.WARNING):
        playbook, _lessons, _hints = parse_coach_sections(truncated)

    assert playbook == ""
    assert any("truncated" in r.message for r in caplog.records)
