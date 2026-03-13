"""Tests for book ingestion pipeline: chunking, registration, validation."""
from __future__ import annotations

import json
from pathlib import Path

from autocontext.knowledge.ingestion import (
    chunk_markdown,
    register_book,
    slugify,
    validate_ingestion,
)


def _pad(text: str, target_chars: int = 25000) -> str:
    """Pad text to exceed the 6k token (~24k char) small-file threshold."""
    padding = "\n\nLorem ipsum dolor sit amet. " * ((target_chars - len(text)) // 30 + 1)
    return text + padding


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify() -> None:
    assert slugify("The Stable Dependencies Principle") == "the-stable-dependencies-principle"
    assert slugify("Chapter 1: Intro") == "chapter-1-intro"
    assert slugify("  Spaces  &  Symbols!  ") == "spaces-symbols"


# ---------------------------------------------------------------------------
# chunk_markdown
# ---------------------------------------------------------------------------


def test_chunk_single_h1() -> None:
    md = _pad("# Chapter 1\n\nSome content here.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 1
    assert chunks[0]["title"] == "Chapter 1"
    assert chunks[0]["chapter"] == 1
    assert "Some content here." in chunks[0]["content"]


def test_chunk_multiple_h1() -> None:
    md = _pad("# Chapter 1\n\nFirst.\n") + "\n# Chapter 2\n\n" + _pad("Second.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 2
    assert chunks[0]["title"] == "Chapter 1"
    assert chunks[1]["title"] == "Chapter 2"
    assert "First." in chunks[0]["content"]
    assert "Second." in chunks[1]["content"]


def test_chunk_h2_sections() -> None:
    md = _pad("# Chapter 1\n\n## Section A\n\nContent A.\n") + "\n## Section B\n\n" + _pad("Content B.\n")
    chunks = chunk_markdown(md, book_name="test")
    # H1 preamble + Section A in first chunk, Section B in second
    assert len(chunks) >= 2
    section_chunks = [c for c in chunks if c["section"] > 0]
    assert len(section_chunks) >= 2


def test_chunk_preserves_tables() -> None:
    md = _pad("# Chapter 1\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n")
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 1
    assert "| 3 | 4 |" in chunks[0]["content"]


def test_chunk_preserves_code_blocks() -> None:
    md = _pad("# Chapter 1\n\n```python\ndef foo():\n    return 42\n```\n")
    chunks = chunk_markdown(md, book_name="test")
    assert "def foo():" in chunks[0]["content"]


def test_chunk_preserves_math_blocks() -> None:
    md = _pad("# Chapter 1\n\n$$\nE = mc^2\n$$\n\nSome text.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert "E = mc^2" in chunks[0]["content"]


def test_chunk_preserves_blockquotes() -> None:
    md = _pad("# Chapter 1\n\n> This is a quote\n> that spans lines\n\nAfter.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert "> This is a quote" in chunks[0]["content"]
    assert "> that spans lines" in chunks[0]["content"]


def test_chunk_preserves_lists() -> None:
    md = _pad("# Chapter 1\n\n- Item 1\n  - Sub item\n- Item 2\n\nAfter.\n")
    chunks = chunk_markdown(md, book_name="test")
    content = chunks[0]["content"]
    assert "- Item 1" in content
    assert "- Item 2" in content


def test_chunk_small_file_no_split() -> None:
    md = "Just a small file with no headings.\n\nAnother paragraph.\n"
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 1
    assert chunks[0]["title"] == "test"
    assert chunks[0]["chapter"] == 1


def test_chunk_frontmatter_format() -> None:
    md = _pad("# My Chapter\n\nContent.\n")
    chunks = chunk_markdown(md, book_name="my-book")
    assert chunks[0]["book"] == "my-book"
    assert "chapter" in chunks[0]
    assert "title" in chunks[0]


# ---------------------------------------------------------------------------
# register_book
# ---------------------------------------------------------------------------


def test_register_book(tmp_path: Path) -> None:
    library_root = tmp_path / "_library"
    book_md = tmp_path / "book.md"
    # Use padded content so chunking actually splits on headings
    book_md.write_text(_pad("# Chapter 1\n\nContent.\n") + "\n# Chapter 2\n\n" + _pad("More.\n"))

    result = register_book(
        source_path=book_md,
        library_root=library_root,
        book_name="test-book",
        title="Test Book",
        author="Test Author",
        tags=["test"],
    )
    assert result["name"] == "test-book"
    assert (library_root / "books" / "test-book" / "book.md").exists()
    assert (library_root / "books" / "test-book" / "chapters").is_dir()
    assert (library_root / "books" / "test-book" / "meta.json").exists()

    meta = json.loads((library_root / "books" / "test-book" / "meta.json").read_text())
    assert meta["title"] == "Test Book"
    assert meta["author"] == "Test Author"
    assert meta["chapter_count"] == 2
    assert "test" in meta["tags"]


def test_register_book_with_images(tmp_path: Path) -> None:
    library_root = tmp_path / "_library"
    book_md = tmp_path / "book.md"
    book_md.write_text("# Ch1\n\n![fig](images/fig1.png)\n")
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "fig1.png").write_bytes(b"PNG")

    result = register_book(
        source_path=book_md,
        library_root=library_root,
        book_name="img-book",
        title="Image Book",
        images_path=images_dir,
    )
    assert (library_root / "books" / "img-book" / "images" / "fig1.png").exists()
    meta = json.loads((library_root / "books" / "img-book" / "meta.json").read_text())
    assert meta["image_count"] == 1


def test_register_book_duplicate_fails(tmp_path: Path) -> None:
    library_root = tmp_path / "_library"
    book_md = tmp_path / "book.md"
    book_md.write_text("# Ch1\n\nContent.\n")

    register_book(
        source_path=book_md,
        library_root=library_root,
        book_name="dup",
        title="First",
    )
    try:
        register_book(
            source_path=book_md,
            library_root=library_root,
            book_name="dup",
            title="Second",
        )
        assert False, "Should have raised"
    except FileExistsError:
        pass


# ---------------------------------------------------------------------------
# validate_ingestion
# ---------------------------------------------------------------------------


def test_validate_ingestion_missing_reference(tmp_path: Path) -> None:
    book_dir = tmp_path / "books" / "test"
    book_dir.mkdir(parents=True)
    chapters_dir = book_dir / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch01-intro.md").write_text("---\ntitle: Intro\n---\nContent\n")

    errors = validate_ingestion(book_dir)
    assert any("reference.md" in e for e in errors)


def test_validate_ingestion_success(tmp_path: Path) -> None:
    book_dir = tmp_path / "books" / "test"
    book_dir.mkdir(parents=True)
    (book_dir / "reference.md").write_text("# Core Thesis\n\nContent " * 100)
    chapters_dir = book_dir / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch01-intro.md").write_text("---\ntitle: Intro\n---\nContent\n")

    errors = validate_ingestion(book_dir)
    assert errors == []
