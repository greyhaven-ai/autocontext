"""Tests for library CLI commands: add-book, list-books, remove-book."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# add-book
# ---------------------------------------------------------------------------


def test_add_book_command(tmp_path: Path, monkeypatch: object) -> None:
    book = tmp_path / "test.md"
    book.write_text("# Chapter 1\n\nContent.\n")
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_ROOT", str(tmp_path / "library"))  # type: ignore[attr-defined]
    result = runner.invoke(app, ["add-book", str(book), "--title", "Test Book", "--name", "test"])
    # Should get past arg validation even if LLM call fails
    assert result.exit_code != 2  # Not a usage error


# ---------------------------------------------------------------------------
# list-books
# ---------------------------------------------------------------------------


def test_list_books_empty(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_ROOT", str(tmp_path / "library"))  # type: ignore[attr-defined]
    result = runner.invoke(app, ["list-books"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# remove-book
# ---------------------------------------------------------------------------


def test_remove_book_not_found(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_ROOT", str(tmp_path / "library"))  # type: ignore[attr-defined]
    result = runner.invoke(app, ["remove-book", "nonexistent"])
    assert result.exit_code != 0
