"""Tests for consult_library tool handler: routing, rate limiting, logging."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autocontext.agents.library_tool import LibraryToolHandler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def library_root(tmp_path: Path) -> Path:
    root = tmp_path / "_library"
    root.mkdir(parents=True)
    return root


def _make_handler(
    library_root: Path,
    books: dict[str, str] | None = None,
    max_consults: int = 3,
) -> LibraryToolHandler:
    """Create a handler with mock librarians."""
    mock_librarians: dict = {}
    if books:
        for name, ref_content in books.items():
            book_dir = library_root / "books" / name
            book_dir.mkdir(parents=True)
            (book_dir / "reference.md").write_text(ref_content)
            mock = MagicMock()
            mock.consult.return_value = f"Answer from {name}"
            mock_librarians[name] = mock

    return LibraryToolHandler(
        librarians=mock_librarians,
        library_root=library_root,
        max_consults_per_role=max_consults,
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_consult_specific_book(library_root: Path) -> None:
    handler = _make_handler(library_root, {"clean-arch": "SRP principles..."})
    result = handler.handle(
        question="What about SRP?",
        book_name="clean-arch",
        calling_role="analyst",
        generation=1,
    )
    assert "Answer from clean-arch" in result["answer"]
    handler.librarians["clean-arch"].consult.assert_called_once()


def test_consult_unknown_book(library_root: Path) -> None:
    handler = _make_handler(library_root, {"clean-arch": "content"})
    result = handler.handle(
        question="What?",
        book_name="nonexistent",
        calling_role="analyst",
        generation=1,
    )
    assert "error" in result


def test_consult_no_book_name_routes_to_all(library_root: Path) -> None:
    handler = _make_handler(library_root, {"a": "content a", "b": "content b"})
    result = handler.handle(
        question="General question",
        book_name=None,
        calling_role="coach",
        generation=1,
    )
    assert "answer" in result


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_consult_rate_limit(library_root: Path) -> None:
    handler = _make_handler(library_root, {"clean-arch": "content"}, max_consults=2)

    handler.handle(question="Q1", book_name="clean-arch", calling_role="analyst", generation=1)
    handler.handle(question="Q2", book_name="clean-arch", calling_role="analyst", generation=1)
    result = handler.handle(question="Q3", book_name="clean-arch", calling_role="analyst", generation=1)
    assert "limit" in result.get("error", "").lower() or "exceeded" in result.get("error", "").lower()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_consult_logs_query(library_root: Path) -> None:
    handler = _make_handler(library_root, {"clean-arch": "content"})
    handler.handle(question="Test Q", book_name="clean-arch", calling_role="analyst", generation=1)
    assert len(handler.consultation_log) == 1
    assert handler.consultation_log[0]["question"] == "Test Q"
    assert handler.consultation_log[0]["calling_role"] == "analyst"
