"""Book ingestion pipeline: markdown normalization, chunking, and registration."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _is_atomic_line(line: str, in_block: dict) -> bool:
    """Track whether we're inside an atomic block."""
    stripped = line.strip()

    # Fenced code blocks
    if stripped.startswith("```"):
        in_block["code"] = not in_block.get("code", False)
        return True
    if in_block.get("code"):
        return True

    # Math blocks
    if stripped.startswith("$$"):
        in_block["math"] = not in_block.get("math", False)
        return True
    if in_block.get("math"):
        return True

    # Tables
    if stripped.startswith("|"):
        in_block["table"] = True
        return True
    elif in_block.get("table"):
        in_block["table"] = False

    # Blockquotes
    if stripped.startswith(">"):
        in_block["blockquote"] = True
        return True
    elif in_block.get("blockquote") and stripped:
        in_block["blockquote"] = False

    # Lists
    if re.match(r"^[\s]*[-*+]\s|^[\s]*\d+\.\s", line):
        in_block["list"] = True
        return True
    elif in_block.get("list"):
        if stripped == "" or not line[0].isspace():
            in_block["list"] = False

    return False


def chunk_markdown(
    text: str,
    book_name: str,
    max_chunk_tokens: int = 8000,
) -> list[dict]:
    """Split markdown into chunks by heading structure.

    Returns list of dicts with: book, chapter, section, title, content, token_count.
    Files under ~6k tokens are returned as a single chunk.
    """
    if _estimate_tokens(text) < 6000:
        return [
            {
                "book": book_name,
                "chapter": 1,
                "section": 1,
                "title": book_name,
                "content": text,
                "token_count": _estimate_tokens(text),
            }
        ]

    chunks: list[dict] = []
    current_lines: list[str] = []
    current_title = book_name
    chapter_num = 0
    section_num = 0

    lines = text.split("\n")
    in_block: dict = {}

    for line in lines:
        _is_atomic_line(line, in_block)
        any_open = any(in_block.get(k) for k in ("code", "math", "table", "blockquote", "list"))

        is_h1 = line.startswith("# ") and not any_open
        is_h2 = line.startswith("## ") and not line.startswith("### ") and not any_open

        if is_h1 or is_h2:
            # Flush previous chunk
            if current_lines:
                content = "\n".join(current_lines)
                if content.strip():
                    chunks.append(
                        {
                            "book": book_name,
                            "chapter": max(chapter_num, 1),
                            "section": section_num,
                            "title": current_title,
                            "content": content,
                            "token_count": _estimate_tokens(content),
                        }
                    )
                current_lines = []

            if is_h1:
                chapter_num += 1
                section_num = 1
                current_title = line.lstrip("# ").strip()
            else:
                section_num += 1
                current_title = line.lstrip("# ").strip()

        current_lines.append(line)

    # Flush final chunk
    if current_lines:
        content = "\n".join(current_lines)
        if content.strip():
            chunks.append(
                {
                    "book": book_name,
                    "chapter": max(chapter_num, 1),
                    "section": section_num,
                    "title": current_title,
                    "content": content,
                    "token_count": _estimate_tokens(content),
                }
            )

    return chunks if chunks else [
        {
            "book": book_name,
            "chapter": 1,
            "section": 1,
            "title": book_name,
            "content": text,
            "token_count": _estimate_tokens(text),
        }
    ]


def register_book(
    source_path: Path,
    library_root: Path,
    book_name: str,
    title: str,
    author: str = "",
    tags: list[str] | None = None,
    images_path: Path | None = None,
) -> dict:
    """Register a book in the global library.

    Normalizes, chunks, copies source, writes meta.json.
    Does NOT produce reference.md — that requires an LLM call (see ingest_book).
    Raises FileExistsError if book already registered.
    """
    book_dir = library_root / "books" / book_name
    if book_dir.exists():
        raise FileExistsError(f"Book '{book_name}' already exists at {book_dir}")

    text = source_path.read_text(encoding="utf-8")
    chunks = chunk_markdown(text, book_name)

    # Create directory structure
    book_dir.mkdir(parents=True)
    chapters_dir = book_dir / "chapters"
    chapters_dir.mkdir()

    # Copy original
    shutil.copy2(source_path, book_dir / "book.md")

    # Write chunks
    for chunk in chunks:
        slug = slugify(chunk["title"])
        filename = f"ch{chunk['chapter']:02d}-s{chunk['section']:02d}-{slug}.md"
        frontmatter = (
            f"---\n"
            f"book: {chunk['book']}\n"
            f"chapter: {chunk['chapter']}\n"
            f"section: {chunk['section']}\n"
            f"title: \"{chunk['title']}\"\n"
            f"token_count: {chunk['token_count']}\n"
            f"---\n\n"
        )
        (chapters_dir / filename).write_text(frontmatter + chunk["content"], encoding="utf-8")

    # Handle images
    image_count = 0
    image_paths: list[str] = []
    if images_path and images_path.is_dir():
        dest_images = book_dir / "images"
        shutil.copytree(images_path, dest_images)
        for img in dest_images.rglob("*"):
            if img.is_file():
                image_count += 1
                image_paths.append(str(img.relative_to(book_dir)))

    # Write meta.json
    meta = {
        "name": book_name,
        "title": title,
        "author": author,
        "tags": tags or [],
        "token_count": _estimate_tokens(text),
        "chapter_count": max(c["chapter"] for c in chunks),
        "chunk_count": len(chunks),
        "image_count": image_count,
        "image_paths": image_paths,
        "ingestion_date": datetime.now(timezone.utc).isoformat(),
        "has_reference": False,
    }
    (book_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return meta


def validate_ingestion(book_dir: Path) -> list[str]:
    """Validate a book directory after ingestion. Returns list of error messages."""
    errors: list[str] = []

    ref = book_dir / "reference.md"
    if not ref.exists():
        errors.append("reference.md does not exist")
    elif ref.stat().st_size == 0:
        errors.append("reference.md is empty")
    elif _estimate_tokens(ref.read_text(encoding="utf-8")) > 25000:
        errors.append("reference.md exceeds 25k token limit")

    chapters_dir = book_dir / "chapters"
    if not chapters_dir.is_dir() or not list(chapters_dir.glob("*.md")):
        errors.append("No chapter files found")

    return errors


def remove_book(library_root: Path, book_name: str) -> None:
    """Remove a book from the global library."""
    book_dir = library_root / "books" / book_name
    if not book_dir.exists():
        raise FileNotFoundError(f"Book '{book_name}' not found")
    shutil.rmtree(book_dir)


INGESTION_PROMPT = """\
You are reading "{title}" by {author} in its entirety. Your job is to produce a comprehensive \
internal reference document that captures everything someone would need to advise a software \
project based on this book's principles.

Structure your reference as:
1. **Core Thesis** — The book's central argument in 2-3 sentences
2. **Key Principles** — Numbered list of the book's most important rules/heuristics
3. **Chapter Notes** — For each chapter: title, core argument, key takeaways, notable examples
4. **Decision Framework** — When would this book say "do X" vs "do Y"? Extract the decision logic.
5. **Red Lines** — What does this book consider genuinely harmful? What should never be done?

Be thorough. This reference is your permanent memory of this book.

--- BOOK TEXT ---

{book_text}
"""


def ingest_book(
    library_root: Path,
    book_name: str,
    provider: object,
    model: str,
) -> Path:
    """Run the LLM ingestion call to produce reference.md.

    Requires the book to be registered first (via register_book).
    Returns the path to the generated reference.md.
    Raises FileNotFoundError if book is not registered.
    """
    book_dir = library_root / "books" / book_name
    if not book_dir.exists():
        raise FileNotFoundError(f"Book '{book_name}' not registered. Run register_book first.")

    meta_path = book_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    book_text = (book_dir / "book.md").read_text(encoding="utf-8")
    prompt = INGESTION_PROMPT.format(
        title=meta.get("title", book_name),
        author=meta.get("author", "Unknown"),
        book_text=book_text,
    )

    messages = [{"role": "user", "content": prompt}]
    response = provider.query(messages=messages, model=model, max_tokens=8000, temperature=0.2)  # type: ignore[union-attr]

    ref_path = book_dir / "reference.md"
    ref_path.write_text(response.text, encoding="utf-8")

    # Update meta
    meta["has_reference"] = True
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return ref_path


def list_books(library_root: Path) -> list[dict]:
    """List all books in the global library."""
    books_dir = library_root / "books"
    if not books_dir.exists():
        return []
    result = []
    for meta_path in sorted(books_dir.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        result.append(meta)
    return result
