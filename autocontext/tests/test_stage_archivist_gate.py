"""Tests for archivist gate stage: proceed, retry, skip decisions."""
from __future__ import annotations

from autocontext.agents.contracts import ArchivistDecision, ArchivistOutput
from autocontext.loop.stage_archivist_gate import evaluate_archivist_gate


# ---------------------------------------------------------------------------
# No archivist / empty decisions
# ---------------------------------------------------------------------------


def test_no_archivist_output() -> None:
    result = evaluate_archivist_gate(archivist_output=None, backpressure_decision="advance")
    assert result["action"] == "proceed"


def test_empty_decisions() -> None:
    output = ArchivistOutput(raw_markdown="", decisions=[], synthesis="All clear.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="advance")
    assert result["action"] == "proceed"


# ---------------------------------------------------------------------------
# Soft flags
# ---------------------------------------------------------------------------


def test_soft_flag_only() -> None:
    dec = ArchivistDecision(
        flag_source="librarian_a", book_name="A", verdict="soft_flag",
        reasoning="Minor", cited_passage="quote",
    )
    output = ArchivistOutput(raw_markdown="", decisions=[dec], synthesis="One soft flag.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="advance")
    assert result["action"] == "proceed"
    assert len(result["soft_flags"]) == 1


# ---------------------------------------------------------------------------
# Hard gates
# ---------------------------------------------------------------------------


def test_hard_gate_triggers_retry() -> None:
    dec = ArchivistDecision(
        flag_source="librarian_a", book_name="A", verdict="hard_gate",
        reasoning="Critical violation", cited_passage="quote",
    )
    output = ArchivistOutput(raw_markdown="", decisions=[dec], synthesis="Violation found.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="advance")
    assert result["action"] == "retry"
    assert "Critical violation" in result["constraint"]


def test_hard_gate_skipped_on_rollback() -> None:
    dec = ArchivistDecision(
        flag_source="librarian_a", book_name="A", verdict="hard_gate",
        reasoning="Critical", cited_passage="quote",
    )
    output = ArchivistOutput(raw_markdown="", decisions=[dec], synthesis="Violation.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="rollback")
    assert result["action"] == "skip"


def test_dismissed_ignored() -> None:
    dec = ArchivistDecision(
        flag_source="librarian_a", book_name="A", verdict="dismissed",
        reasoning="Not relevant", cited_passage="quote",
    )
    output = ArchivistOutput(raw_markdown="", decisions=[dec], synthesis="Dismissed.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="advance")
    assert result["action"] == "proceed"
    assert result["soft_flags"] == []
