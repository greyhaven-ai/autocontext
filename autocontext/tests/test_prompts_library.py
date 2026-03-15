"""Tests for LibraryPromptBundle and library context block builder."""
from __future__ import annotations

from autocontext.prompts.templates import (
    LibraryPromptBundle,
    build_library_context_block,
    inject_library_context,
)


# ---------------------------------------------------------------------------
# LibraryPromptBundle
# ---------------------------------------------------------------------------


def test_library_prompt_bundle_creation() -> None:
    bundle = LibraryPromptBundle(
        librarian_prompts={"clean-arch": "Review against SRP..."},
        archivist_prompt="Arbitrate these flags...",
        library_context_block="Available books: clean-arch (Clean Architecture)",
    )
    assert "clean-arch" in bundle.librarian_prompts
    assert "Arbitrate" in bundle.archivist_prompt
    assert "Available books" in bundle.library_context_block


# ---------------------------------------------------------------------------
# build_library_context_block
# ---------------------------------------------------------------------------


def test_library_context_block() -> None:
    books = [
        {"name": "clean-arch", "title": "Clean Architecture", "tags": ["architecture"]},
        {"name": "ddd", "title": "Domain-Driven Design", "tags": ["design"]},
    ]
    block = build_library_context_block(books)
    assert "clean-arch" in block
    assert "Clean Architecture" in block
    assert "consult_library" in block


def test_library_context_block_empty() -> None:
    block = build_library_context_block([])
    assert block == ""


# ---------------------------------------------------------------------------
# inject_library_context
# ---------------------------------------------------------------------------


def test_inject_library_context_appends() -> None:
    original_prompt = "You are an analyst. Analyze the strategy."
    books = [{"name": "clean-arch", "title": "Clean Architecture", "tags": ["architecture"]}]
    result = inject_library_context(original_prompt, books)
    assert "Available Literature" in result
    assert "consult_library" in result
    assert original_prompt in result


def test_inject_library_context_no_books() -> None:
    original_prompt = "You are an analyst."
    result = inject_library_context(original_prompt, [])
    assert result == original_prompt
