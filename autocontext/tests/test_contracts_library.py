# tests/test_contracts_library.py
from autocontext.agents.contracts import (
    ArchivistDecision,
    ArchivistOutput,
    LibrarianFlag,
    LibrarianOutput,
)


def test_librarian_flag_fields():
    flag = LibrarianFlag(
        severity="violation",
        description="Couples scoring to movement",
        cited_section="ch03-s02-dependency-inversion",
        recommendation="Invert the dependency",
    )
    assert flag.severity == "violation"
    assert flag.cited_section == "ch03-s02-dependency-inversion"


def test_librarian_output_defaults():
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


def test_librarian_output_with_flags():
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


def test_archivist_decision_fields():
    dec = ArchivistDecision(
        flag_source="librarian_clean_arch",
        book_name="clean-architecture",
        verdict="hard_gate",
        reasoning="The principle clearly applies",
        cited_passage="Original text from book",
    )
    assert dec.verdict == "hard_gate"
    assert dec.flag_source == "librarian_clean_arch"


def test_archivist_output_defaults():
    out = ArchivistOutput(
        raw_markdown="# Synthesis",
        decisions=[],
        synthesis="No issues found",
    )
    assert out.parse_success is True
    assert out.decisions == []


def test_archivist_output_with_decisions():
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
