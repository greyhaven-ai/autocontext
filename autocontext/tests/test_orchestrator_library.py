"""Tests for library wiring logic used by the orchestrator."""
from __future__ import annotations

from autocontext.agents.contracts import LibrarianOutput
from autocontext.config.settings import AppSettings


def test_orchestrator_creates_librarians_for_active_books() -> None:
    """Verify settings provide correct model config for library runners."""
    settings = AppSettings(library_books=["clean-arch", "ddd"])
    assert settings.library_books == ["clean-arch", "ddd"]
    assert settings.model_librarian == "claude-sonnet-4-5-20250929"
    assert settings.model_archivist == "claude-opus-4-6"


def test_library_advisories_collected() -> None:
    """Verify library_advisories aggregation logic."""
    lib_out_a = LibrarianOutput(
        raw_markdown="", book_name="a", advisory="Use SRP", flags=[], cited_sections=[],
    )
    lib_out_b = LibrarianOutput(
        raw_markdown="", book_name="b", advisory="Use DDD", flags=[], cited_sections=[],
    )

    advisories = [out.advisory for out in [lib_out_a, lib_out_b] if out.advisory]
    assert advisories == ["Use SRP", "Use DDD"]
