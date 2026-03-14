"""Tests for LibrarianFlag, LibrarianOutput, ArchivistDecision, ArchivistOutput dataclasses."""
from __future__ import annotations

from autocontext.agents.contracts import (
    ArchivistDecision,
    ArchivistOutput,
    LibrarianFlag,
    LibrarianOutput,
)


# ---------------------------------------------------------------------------
# LibrarianFlag
# ---------------------------------------------------------------------------


def test_librarian_flag_fields() -> None:
    flag = LibrarianFlag(
        severity="violation",
        description="Couples scoring to movement",
        cited_section="ch03-s02-dependency-inversion",
        recommendation="Invert the dependency",
    )
    assert flag.severity == "violation"
    assert flag.cited_section == "ch03-s02-dependency-inversion"


# ---------------------------------------------------------------------------
# LibrarianOutput
# ---------------------------------------------------------------------------


def test_librarian_output_defaults() -> None:
    out = LibrarianOutput(
        raw_markdown="# Advisory",
        book_name="clean-architecture",
        advisory="Use SRP",
        flags=[],
        cited_sections=[],
    )
    assert out.parse_success is True
    assert out.book_name == "clean-architecture"
    assert out.flags == []


def test_librarian_output_with_flags() -> None:
    flag = LibrarianFlag(
        severity="concern",
        description="Minor coupling",
        cited_section="ch05-s01",
        recommendation="Consider separating",
    )
    out = LibrarianOutput(
        raw_markdown="text",
        book_name="ddd",
        advisory="advisory text",
        flags=[flag],
        cited_sections=["ch05-s01"],
    )
    assert len(out.flags) == 1
    assert out.flags[0].severity == "concern"


# ---------------------------------------------------------------------------
# ArchivistDecision
# ---------------------------------------------------------------------------


def test_archivist_decision_fields() -> None:
    dec = ArchivistDecision(
        flag_source="librarian_clean_arch",
        book_name="clean-architecture",
        verdict="hard_gate",
        reasoning="The principle clearly applies",
        cited_passage="Original text from book",
    )
    assert dec.verdict == "hard_gate"
    assert dec.flag_source == "librarian_clean_arch"


# ---------------------------------------------------------------------------
# ArchivistOutput
# ---------------------------------------------------------------------------


def test_archivist_output_defaults() -> None:
    out = ArchivistOutput(
        raw_markdown="# Synthesis",
        decisions=[],
        synthesis="No issues found",
    )
    assert out.parse_success is True
    assert out.decisions == []


def test_archivist_output_with_decisions() -> None:
    dec = ArchivistDecision(
        flag_source="librarian_ddd",
        book_name="ddd",
        verdict="soft_flag",
        reasoning="Valid concern",
        cited_passage="Quote from book",
    )
    out = ArchivistOutput(
        raw_markdown="text",
        decisions=[dec],
        synthesis="One legitimate concern",
    )
    assert len(out.decisions) == 1
    assert out.decisions[0].verdict == "soft_flag"
