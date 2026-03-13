"""Tests for LibrarianRunner output parser."""
from __future__ import annotations

from autocontext.agents.librarian import parse_librarian_output


def test_parse_librarian_no_flags() -> None:
    content = (
        "<!-- ADVISORY_START -->\n"
        "Apply SRP to scoring module.\n"
        "<!-- ADVISORY_END -->\n"
        "\n"
        "<!-- FLAGS_START -->\n"
        "<!-- FLAGS_END -->\n"
    )
    out = parse_librarian_output(content, book_name="clean-arch")
    assert out.advisory == "Apply SRP to scoring module."
    assert out.flags == []
    assert out.parse_success is True


def test_parse_librarian_with_flags() -> None:
    content = (
        "<!-- ADVISORY_START -->\n"
        "Consider dependency inversion.\n"
        "<!-- ADVISORY_END -->\n"
        "\n"
        "<!-- FLAGS_START -->\n"
        "## Flag: [severity: violation]\n"
        "**Section:** ch03-s02-dependency-inversion\n"
        "**Issue:** Scoring couples directly to movement.\n"
        "**Recommendation:** Invert the dependency.\n"
        "<!-- FLAGS_END -->\n"
    )
    out = parse_librarian_output(content, book_name="clean-arch")
    assert len(out.flags) == 1
    assert out.flags[0].severity == "violation"
    assert out.flags[0].cited_section == "ch03-s02-dependency-inversion"
    assert "Invert" in out.flags[0].recommendation


def test_parse_librarian_multiple_flags() -> None:
    content = (
        "<!-- ADVISORY_START -->\nAdvice.\n<!-- ADVISORY_END -->\n"
        "<!-- FLAGS_START -->\n"
        "## Flag: [severity: concern]\n"
        "**Section:** ch01-s01\n"
        "**Issue:** Minor issue.\n"
        "**Recommendation:** Fix it.\n"
        "\n"
        "## Flag: [severity: violation]\n"
        "**Section:** ch05-s03\n"
        "**Issue:** Major issue.\n"
        "**Recommendation:** Rewrite.\n"
        "<!-- FLAGS_END -->\n"
    )
    out = parse_librarian_output(content, book_name="test")
    assert len(out.flags) == 2
    assert out.flags[0].severity == "concern"
    assert out.flags[1].severity == "violation"


def test_parse_librarian_malformed_fallback() -> None:
    content = "Just some unstructured text without markers."
    out = parse_librarian_output(content, book_name="test")
    assert out.parse_success is False
    assert out.advisory == content
    assert out.flags == []


def test_parse_librarian_cited_sections() -> None:
    content = (
        "<!-- ADVISORY_START -->\nUse ch03-s02 principles.\n<!-- ADVISORY_END -->\n"
        "<!-- FLAGS_START -->\n"
        "## Flag: [severity: concern]\n"
        "**Section:** ch03-s02-srp\n"
        "**Issue:** Coupling.\n"
        "**Recommendation:** Decouple.\n"
        "<!-- FLAGS_END -->\n"
    )
    out = parse_librarian_output(content, book_name="test")
    assert "ch03-s02-srp" in out.cited_sections
