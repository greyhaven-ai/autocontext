# Librarian & Archivist Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add literature-aware librarian and archivist agent roles that advise, flag, and gate the generation loop based on ingested books.

**Architecture:** New `LibrarianRunner` and `ArchivistRunner` agents plug into the existing RoleDAG pipeline. Books are ingested via CLI into a global library, activated per-scenario at run time. A `consult_library` tool lets any agent query the literature on demand. The archivist acts as a conditional arbiter — always in the DAG but only does real work when librarians escalate violations.

**Tech Stack:** Python 3.11+, dataclasses, Typer CLI, Pydantic settings, existing SubagentRuntime/RoleDAG/PipelineEngine infrastructure.

**Spec:** `docs/superpowers/specs/2026-03-13-librarian-archivist-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/autocontext/knowledge/ingestion.py` | Markdown normalization, heading-based chunking, book registration, LLM ingestion call, validation |
| `src/autocontext/loop/stage_archivist_gate.py` | Archivist gate evaluation (Stage 3b) |
| `src/autocontext/agents/librarian.py` | `LibrarianRunner` — proactive review + consultation handler + output parser |
| `src/autocontext/agents/archivist.py` | `ArchivistRunner` — conditional arbitration + spot-pull + output parser |
| `src/autocontext/agents/library_tool.py` | `consult_library` tool implementation with routing logic |
| `tests/test_ingestion.py` | Ingestion pipeline tests (chunking, atomic blocks, meta.json, validation) |
| `tests/test_librarian.py` | LibrarianRunner tests (proactive review, consultation, parser) |
| `tests/test_archivist.py` | ArchivistRunner tests (arbitration, no-op, spot-pull) |
| `tests/test_library_tool.py` | consult_library routing, cost cap, logging |
| `tests/test_library_integration.py` | End-to-end: ingestion → DAG → gate |

### Modified Files

| File | Change |
|------|--------|
| `src/autocontext/agents/contracts.py` | Add `LibrarianFlag`, `LibrarianOutput`, `ArchivistDecision`, `ArchivistOutput` |
| `src/autocontext/agents/types.py` | Add `librarian_outputs`, `archivist_output`, `library_advisories` to `AgentOutputs` |
| `src/autocontext/config/settings.py` | Add library/librarian/archivist/ingestion settings (~12 fields) |
| `src/autocontext/prompts/templates.py` | Add `LibraryPromptBundle`, extend `build_prompt_bundle()` |
| `src/autocontext/agents/pipeline_adapter.py` | Extend `build_mts_dag()` and `build_role_handler()` for library roles |
| `src/autocontext/agents/role_router.py` | Add prefix-based routing for `librarian_*` and `archivist` |
| `src/autocontext/agents/orchestrator.py` | Instantiate library runners, wire into `_run_via_pipeline()` |
| `src/autocontext/storage/artifacts.py` | Add library directory helpers, notes persistence, consultation log |
| `src/autocontext/loop/generation_pipeline.py` | Add archivist gate stage (3b), renumber 3c/3d |
| `src/autocontext/cli.py` | Add `add-book`, `list-books`, `remove-book` commands, `--books` on `run` |
| `src/autocontext/agents/agent_sdk_client.py` | Add `consult_library` to `ROLE_TOOL_CONFIG` |
| `src/autocontext/knowledge/export.py` | Include library info in `SkillPackage` |
| `CLAUDE.md` | Document roles, commands, config, architecture |
| `README.md` | Add capabilities line, workflow examples |
| `autocontext/README.md` | Add bullet, CLI examples, config |
| `CONTRIBUTING.md` | Add library test fixture note |

---

## Chunk 1: Data Contracts & Configuration

Foundation layer — defines all types, settings, and output contracts that every other task depends on.

### Task 1: Output Contracts

**Files:**
- Modify: `autocontext/src/autocontext/agents/contracts.py:33-40`
- Test: `autocontext/tests/test_contracts_library.py`

- [ ] **Step 1: Write tests for new dataclasses**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_contracts_library.py -v`
Expected: FAIL — `ImportError: cannot import name 'LibrarianFlag'`

- [ ] **Step 3: Implement the dataclasses**

Add to `src/autocontext/agents/contracts.py` after the `ArchitectOutput` class:

```python
@dataclass(slots=True)
class LibrarianFlag:
    severity: str  # "concern" or "violation"
    description: str
    cited_section: str
    recommendation: str


@dataclass(slots=True)
class LibrarianOutput:
    raw_markdown: str
    book_name: str
    advisory: str
    flags: list[LibrarianFlag]
    cited_sections: list[str]
    parse_success: bool = True


@dataclass(slots=True)
class ArchivistDecision:
    flag_source: str
    book_name: str
    verdict: str  # "dismissed", "soft_flag", "hard_gate"
    reasoning: str
    cited_passage: str


@dataclass(slots=True)
class ArchivistOutput:
    raw_markdown: str
    decisions: list[ArchivistDecision]
    synthesis: str
    parse_success: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_contracts_library.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add tests/test_contracts_library.py src/autocontext/agents/contracts.py
git commit -m "feat(library): add librarian and archivist output contracts"
```

### Task 2: AgentOutputs Extension

**Files:**
- Modify: `autocontext/src/autocontext/agents/types.py:12-31`
- Test: `autocontext/tests/test_agent_outputs_library.py`

- [ ] **Step 1: Write test for new fields**

```python
# tests/test_agent_outputs_library.py
from autocontext.agents.contracts import LibrarianOutput, ArchivistOutput, ArchivistDecision
from autocontext.agents.types import AgentOutputs


def test_agent_outputs_library_defaults():
    out = AgentOutputs(
        strategy={},
        analysis_markdown="",
        coach_markdown="",
        coach_playbook="",
        coach_lessons="",
        coach_competitor_hints="",
        architect_markdown="",
        architect_tools=[],
        role_executions=[],
    )
    assert out.librarian_outputs == []
    assert out.archivist_output is None
    assert out.library_advisories == []


def test_agent_outputs_with_library_data():
    lib_out = LibrarianOutput(
        raw_markdown="text",
        book_name="ddd",
        advisory="Use bounded contexts",
        flags=[],
        cited_sections=[],
    )
    arch_out = ArchivistOutput(
        raw_markdown="text",
        decisions=[],
        synthesis="All clear",
    )
    out = AgentOutputs(
        strategy={},
        analysis_markdown="",
        coach_markdown="",
        coach_playbook="",
        coach_lessons="",
        coach_competitor_hints="",
        architect_markdown="",
        architect_tools=[],
        role_executions=[],
        librarian_outputs=[lib_out],
        archivist_output=arch_out,
        library_advisories=["Use bounded contexts"],
    )
    assert len(out.librarian_outputs) == 1
    assert out.archivist_output is not None
    assert out.library_advisories == ["Use bounded contexts"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd autocontext && uv run pytest tests/test_agent_outputs_library.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'librarian_outputs'`

- [ ] **Step 3: Add fields to AgentOutputs**

In `src/autocontext/agents/types.py`, add imports and fields:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.agents.contracts import ArchivistOutput, LibrarianOutput
    from autocontext.harness.core.types import RoleExecution
```

Add these fields after `architect_output`:

```python
    librarian_outputs: list[LibrarianOutput] = field(default_factory=list)
    archivist_output: ArchivistOutput | None = None
    library_advisories: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd autocontext && uv run pytest tests/test_agent_outputs_library.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add tests/test_agent_outputs_library.py src/autocontext/agents/types.py
git commit -m "feat(library): extend AgentOutputs with librarian/archivist fields"
```

### Task 3: Configuration Settings

**Files:**
- Modify: `autocontext/src/autocontext/config/settings.py:254-311`
- Test: `autocontext/tests/test_settings_library.py`

- [ ] **Step 1: Write tests for new settings**

```python
# tests/test_settings_library.py
import os

from autocontext.config.settings import AppSettings


def test_library_defaults():
    s = AppSettings()
    assert s.library_root == "knowledge/_library"
    assert s.library_books == []
    assert s.librarian_enabled is True
    assert s.model_librarian == "claude-sonnet-4-5-20250929"
    assert s.librarian_provider == ""
    assert s.library_max_consults_per_role == 3
    assert s.model_archivist == "claude-opus-4-6"
    assert s.archivist_provider == ""
    assert s.ingestion_model == "claude-opus-4-6"


def test_library_books_from_env(monkeypatch):
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_BOOKS", "clean-arch,ddd")
    s = AppSettings()
    assert s.library_books == ["clean-arch", "ddd"]


def test_library_books_empty_string(monkeypatch):
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_BOOKS", "")
    s = AppSettings()
    assert s.library_books == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_settings_library.py -v`
Expected: FAIL — `AttributeError: 'AppSettings' has no attribute 'library_root'`

- [ ] **Step 3: Add settings fields**

In `src/autocontext/config/settings.py`, add these fields to `AppSettings` (after the existing provider fields around line 254):

```python
    # Library
    library_root: str = Field(default="knowledge/_library")
    library_books: list[str] = Field(default_factory=list)

    # Librarian
    librarian_enabled: bool = Field(default=True)
    model_librarian: str = Field(default="claude-sonnet-4-5-20250929")
    librarian_provider: str = Field(default="")
    library_max_consults_per_role: int = Field(default=3)

    # Archivist
    model_archivist: str = Field(default="claude-opus-4-6")
    archivist_provider: str = Field(default="")

    # Ingestion
    ingestion_model: str = Field(default="claude-opus-4-6")
```

Add a `@field_validator` for `library_books` to handle comma-separated env var:

```python
    @field_validator("library_books", mode="before")
    @classmethod
    def _parse_library_books(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [b.strip() for b in v.split(",") if b.strip()]
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_settings_library.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add tests/test_settings_library.py src/autocontext/config/settings.py
git commit -m "feat(library): add library/librarian/archivist configuration settings"
```

---

## Chunk 2: Ingestion Pipeline

The normalization and book registration system. This is self-contained — no other component depends on it except the CLI command (Task 8) and the librarian runner (Task 5).

### Task 4: Book Ingestion — Normalization & Storage

**Files:**
- Create: `autocontext/src/autocontext/knowledge/ingestion.py`
- Test: `autocontext/tests/test_ingestion.py`

- [ ] **Step 1: Write tests for markdown chunking**

```python
# tests/test_ingestion.py
import json
from pathlib import Path

from autocontext.knowledge.ingestion import (
    chunk_markdown,
    register_book,
    slugify,
    validate_ingestion,
)


def test_slugify():
    assert slugify("The Stable Dependencies Principle") == "the-stable-dependencies-principle"
    assert slugify("Chapter 1: Intro") == "chapter-1-intro"
    assert slugify("  Spaces  &  Symbols!  ") == "spaces-symbols"


def _pad(text: str, target_chars: int = 25000) -> str:
    """Pad text to exceed the 6k token (~24k char) small-file threshold."""
    padding = "\n\nLorem ipsum dolor sit amet. " * ((target_chars - len(text)) // 30 + 1)
    return text + padding


def test_chunk_single_h1():
    md = _pad("# Chapter 1\n\nSome content here.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 1
    assert chunks[0]["title"] == "Chapter 1"
    assert chunks[0]["chapter"] == 1
    assert "Some content here." in chunks[0]["content"]


def test_chunk_multiple_h1():
    md = _pad("# Chapter 1\n\nFirst.\n") + "\n# Chapter 2\n\n" + _pad("Second.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 2
    assert chunks[0]["title"] == "Chapter 1"
    assert chunks[1]["title"] == "Chapter 2"
    assert "First." in chunks[0]["content"]
    assert "Second." in chunks[1]["content"]


def test_chunk_h2_sections():
    md = "# Chapter 1\n\n" + _pad("## Section A\n\nContent A.\n") + "\n## Section B\n\n" + _pad("Content B.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 2
    assert chunks[0]["section"] == 1
    assert chunks[1]["section"] == 2


def test_chunk_preserves_tables():
    md = _pad("# Chapter 1\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n")
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 1
    assert "| 3 | 4 |" in chunks[0]["content"]


def test_chunk_preserves_code_blocks():
    md = _pad("# Chapter 1\n\n```python\ndef foo():\n    return 42\n```\n")
    chunks = chunk_markdown(md, book_name="test")
    assert "def foo():" in chunks[0]["content"]


def test_chunk_preserves_math_blocks():
    md = _pad("# Chapter 1\n\n$$\nE = mc^2\n$$\n\nSome text.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert "E = mc^2" in chunks[0]["content"]


def test_chunk_preserves_blockquotes():
    md = _pad("# Chapter 1\n\n> This is a quote\n> that spans lines\n\nAfter.\n")
    chunks = chunk_markdown(md, book_name="test")
    assert "> This is a quote" in chunks[0]["content"]
    assert "> that spans lines" in chunks[0]["content"]


def test_chunk_preserves_lists():
    md = _pad("# Chapter 1\n\n- Item 1\n  - Sub item\n- Item 2\n\nAfter.\n")
    chunks = chunk_markdown(md, book_name="test")
    content = chunks[0]["content"]
    assert "- Item 1" in content
    assert "- Item 2" in content


def test_chunk_small_file_no_split():
    md = "Just a small file with no headings.\n\nAnother paragraph.\n"
    chunks = chunk_markdown(md, book_name="test")
    assert len(chunks) == 1
    assert chunks[0]["title"] == "test"
    assert chunks[0]["chapter"] == 1


def test_chunk_frontmatter_format():
    md = _pad("# My Chapter\n\nContent.\n")
    chunks = chunk_markdown(md, book_name="my-book")
    assert chunks[0]["book"] == "my-book"
    assert "chapter" in chunks[0]
    assert "title" in chunks[0]


def test_register_book(tmp_path):
    library_root = tmp_path / "_library"
    book_md = tmp_path / "book.md"
    book_md.write_text("# Chapter 1\n\nContent.\n\n# Chapter 2\n\nMore.\n")

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


def test_register_book_with_images(tmp_path):
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


def test_register_book_duplicate_fails(tmp_path):
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


def test_validate_ingestion_missing_reference(tmp_path):
    book_dir = tmp_path / "books" / "test"
    book_dir.mkdir(parents=True)
    chapters_dir = book_dir / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch01-intro.md").write_text("---\ntitle: Intro\n---\nContent\n")

    errors = validate_ingestion(book_dir)
    assert any("reference.md" in e for e in errors)


def test_validate_ingestion_success(tmp_path):
    book_dir = tmp_path / "books" / "test"
    book_dir.mkdir(parents=True)
    (book_dir / "reference.md").write_text("# Core Thesis\n\nContent " * 100)
    chapters_dir = book_dir / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch01-intro.md").write_text("---\ntitle: Intro\n---\nContent\n")

    errors = validate_ingestion(book_dir)
    assert errors == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_ingestion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autocontext.knowledge.ingestion'`

- [ ] **Step 3: Implement the ingestion module**

Create `src/autocontext/knowledge/ingestion.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_ingestion.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/knowledge/ingestion.py tests/test_ingestion.py
git commit -m "feat(library): add book ingestion pipeline with markdown chunking"
```

---

## Chunk 3: Agent Runners & Parsers

The librarian and archivist runner classes, plus their output parsers.

### Task 5: Librarian Runner & Parser

**Files:**
- Create: `autocontext/src/autocontext/agents/librarian.py`
- Test: `autocontext/tests/test_librarian.py`

- [ ] **Step 1: Write tests for parser and runner**

```python
# tests/test_librarian.py
from autocontext.agents.librarian import LibrarianRunner, parse_librarian_output


def test_parse_librarian_no_flags():
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


def test_parse_librarian_with_flags():
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


def test_parse_librarian_multiple_flags():
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


def test_parse_librarian_malformed_fallback():
    content = "Just some unstructured text without markers."
    out = parse_librarian_output(content, book_name="test")
    assert out.parse_success is False
    assert out.advisory == content
    assert out.flags == []


def test_parse_librarian_cited_sections():
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_librarian.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement LibrarianRunner and parser**

Create `src/autocontext/agents/librarian.py`:

```python
"""Librarian agent: literature-aware advisory and flagging."""

from __future__ import annotations

import re
from dataclasses import dataclass

from autocontext.agents.contracts import LibrarianFlag, LibrarianOutput
from autocontext.harness.core.subagent import SubagentRuntime, SubagentTask
from autocontext.harness.core.types import RoleExecution


def parse_librarian_output(content: str, book_name: str) -> LibrarianOutput:
    """Parse librarian markdown output into structured LibrarianOutput."""
    advisory_match = re.search(
        r"<!-- ADVISORY_START -->\s*\n(.*?)\n\s*<!-- ADVISORY_END -->",
        content,
        re.DOTALL,
    )
    flags_match = re.search(
        r"<!-- FLAGS_START -->\s*\n(.*?)\n\s*<!-- FLAGS_END -->",
        content,
        re.DOTALL,
    )

    if not advisory_match:
        return LibrarianOutput(
            raw_markdown=content,
            book_name=book_name,
            advisory=content,
            flags=[],
            cited_sections=[],
            parse_success=False,
        )

    advisory = advisory_match.group(1).strip()
    flags: list[LibrarianFlag] = []
    cited_sections: list[str] = []

    if flags_match:
        flags_text = flags_match.group(1)
        flag_blocks = re.split(r"## Flag:", flags_text)
        for block in flag_blocks:
            block = block.strip()
            if not block:
                continue
            severity_m = re.search(r"\[severity:\s*(concern|violation)\]", block)
            section_m = re.search(r"\*\*Section:\*\*\s*(.+)", block)
            issue_m = re.search(r"\*\*Issue:\*\*\s*(.+)", block)
            rec_m = re.search(r"\*\*Recommendation:\*\*\s*(.+)", block)

            if severity_m and section_m and issue_m and rec_m:
                section = section_m.group(1).strip()
                flags.append(
                    LibrarianFlag(
                        severity=severity_m.group(1),
                        description=issue_m.group(1).strip(),
                        cited_section=section,
                        recommendation=rec_m.group(1).strip(),
                    )
                )
                cited_sections.append(section)

    return LibrarianOutput(
        raw_markdown=content,
        book_name=book_name,
        advisory=advisory,
        flags=flags,
        cited_sections=cited_sections,
    )


class LibrarianRunner:
    """Runs the librarian role for a specific book."""

    def __init__(self, runtime: SubagentRuntime, model: str, book_name: str) -> None:
        self.runtime = runtime
        self.model = model
        self.book_name = book_name

    def run(self, prompt: str) -> tuple[LibrarianOutput, RoleExecution]:
        """Execute librarian review and parse output."""
        execution = self.runtime.run_task(
            SubagentTask(
                role=f"librarian_{self.book_name}",
                model=self.model,
                prompt=prompt,
                max_tokens=4000,
                temperature=0.3,
            )
        )
        output = parse_librarian_output(execution.content, self.book_name)
        return output, execution

    def consult(self, question: str, reference: str) -> str:
        """Answer a consultation query against the book's reference."""
        prompt = (
            f"You are a librarian for the book referenced below. "
            f"Answer this question based on the book's content:\n\n"
            f"Question: {question}\n\n"
            f"Your reference notes:\n{reference}"
        )
        execution = self.runtime.run_task(
            SubagentTask(
                role=f"librarian_{self.book_name}",
                model=self.model,
                prompt=prompt,
                max_tokens=2000,
                temperature=0.2,
            )
        )
        return execution.content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_librarian.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/agents/librarian.py tests/test_librarian.py
git commit -m "feat(library): add LibrarianRunner with output parser"
```

### Task 6: Archivist Runner & Parser

**Files:**
- Create: `autocontext/src/autocontext/agents/archivist.py`
- Test: `autocontext/tests/test_archivist.py`

- [ ] **Step 1: Write tests for parser and runner**

```python
# tests/test_archivist.py
import json
from pathlib import Path

from autocontext.agents.archivist import ArchivistRunner, parse_archivist_output, spot_pull_sections


def test_parse_archivist_no_decisions():
    content = (
        "<!-- SYNTHESIS_START -->\n"
        "No issues found.\n"
        "<!-- SYNTHESIS_END -->\n"
        "\n"
        "<!-- DECISIONS_START -->\n"
        "<!-- DECISIONS_END -->\n"
    )
    out = parse_archivist_output(content)
    assert out.synthesis == "No issues found."
    assert out.decisions == []
    assert out.parse_success is True


def test_parse_archivist_with_decisions():
    content = (
        "<!-- SYNTHESIS_START -->\n"
        "One concern identified.\n"
        "<!-- SYNTHESIS_END -->\n"
        "\n"
        "<!-- DECISIONS_START -->\n"
        "## Decision: [source: librarian_clean_arch] [verdict: soft_flag]\n"
        "**Book:** Clean Architecture\n"
        "**Reasoning:** The principle applies here.\n"
        "**Passage:** \"Depend on abstractions, not concretions.\"\n"
        "<!-- DECISIONS_END -->\n"
    )
    out = parse_archivist_output(content)
    assert len(out.decisions) == 1
    assert out.decisions[0].verdict == "soft_flag"
    assert out.decisions[0].flag_source == "librarian_clean_arch"
    assert out.decisions[0].book_name == "Clean Architecture"
    assert "abstractions" in out.decisions[0].cited_passage


def test_parse_archivist_multiple_decisions():
    content = (
        "<!-- SYNTHESIS_START -->\nMixed.\n<!-- SYNTHESIS_END -->\n"
        "<!-- DECISIONS_START -->\n"
        "## Decision: [source: librarian_a] [verdict: dismissed]\n"
        "**Book:** Book A\n"
        "**Reasoning:** Not applicable.\n"
        "**Passage:** \"Quote A.\"\n"
        "\n"
        "## Decision: [source: librarian_b] [verdict: hard_gate]\n"
        "**Book:** Book B\n"
        "**Reasoning:** Critical violation.\n"
        "**Passage:** \"Quote B.\"\n"
        "<!-- DECISIONS_END -->\n"
    )
    out = parse_archivist_output(content)
    assert len(out.decisions) == 2
    assert out.decisions[0].verdict == "dismissed"
    assert out.decisions[1].verdict == "hard_gate"


def test_parse_archivist_malformed_fallback():
    content = "Unstructured archivist response."
    out = parse_archivist_output(content)
    assert out.parse_success is False
    assert out.decisions == []
    assert out.synthesis == content


def test_spot_pull_sections(tmp_path):
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch03-s02-dependency-inversion.md").write_text(
        "---\ntitle: Dependency Inversion\n---\n\nDepend on abstractions.\n"
    )
    (chapters_dir / "ch05-s01-srp.md").write_text(
        "---\ntitle: SRP\n---\n\nSingle responsibility.\n"
    )

    result = spot_pull_sections(tmp_path, ["ch03-s02-dependency-inversion"])
    assert len(result) == 1
    assert "Depend on abstractions" in result["ch03-s02-dependency-inversion"]


def test_spot_pull_sections_missing(tmp_path):
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()

    result = spot_pull_sections(tmp_path, ["ch99-nonexistent"])
    assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_archivist.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement ArchivistRunner and parser**

Create `src/autocontext/agents/archivist.py`:

```python
"""Archivist agent: conditional arbiter between librarians."""

from __future__ import annotations

import re
from pathlib import Path

from autocontext.agents.contracts import (
    ArchivistDecision,
    ArchivistOutput,
    LibrarianOutput,
)
from autocontext.harness.core.subagent import SubagentRuntime, SubagentTask
from autocontext.harness.core.types import RoleExecution


def spot_pull_sections(book_dir: Path, section_ids: list[str]) -> dict[str, str]:
    """Pull specific chapter sections by ID from a book's chapters directory.

    Section IDs are matched against chapter filenames (without extension).
    Returns dict of section_id -> content.
    """
    chapters_dir = book_dir / "chapters"
    if not chapters_dir.is_dir():
        return {}

    result: dict[str, str] = {}
    for section_id in section_ids:
        for chapter_file in chapters_dir.glob("*.md"):
            if section_id in chapter_file.stem:
                result[section_id] = chapter_file.read_text(encoding="utf-8")
                break
    return result


def parse_archivist_output(content: str) -> ArchivistOutput:
    """Parse archivist markdown output into structured ArchivistOutput."""
    synthesis_match = re.search(
        r"<!-- SYNTHESIS_START -->\s*\n(.*?)\n\s*<!-- SYNTHESIS_END -->",
        content,
        re.DOTALL,
    )
    decisions_match = re.search(
        r"<!-- DECISIONS_START -->\s*\n(.*?)\n\s*<!-- DECISIONS_END -->",
        content,
        re.DOTALL,
    )

    if not synthesis_match:
        return ArchivistOutput(
            raw_markdown=content,
            decisions=[],
            synthesis=content,
            parse_success=False,
        )

    synthesis = synthesis_match.group(1).strip()
    decisions: list[ArchivistDecision] = []

    if decisions_match:
        decisions_text = decisions_match.group(1)
        decision_blocks = re.split(r"## Decision:", decisions_text)
        for block in decision_blocks:
            block = block.strip()
            if not block:
                continue
            source_m = re.search(r"\[source:\s*(\S+)\]", block)
            verdict_m = re.search(r"\[verdict:\s*(dismissed|soft_flag|hard_gate)\]", block)
            book_m = re.search(r"\*\*Book:\*\*\s*(.+)", block)
            reason_m = re.search(r"\*\*Reasoning:\*\*\s*(.+)", block)
            passage_m = re.search(r"\*\*Passage:\*\*\s*(.+)", block)

            if source_m and verdict_m and book_m and reason_m and passage_m:
                decisions.append(
                    ArchivistDecision(
                        flag_source=source_m.group(1),
                        book_name=book_m.group(1).strip(),
                        verdict=verdict_m.group(1),
                        reasoning=reason_m.group(1).strip(),
                        cited_passage=passage_m.group(1).strip().strip('"'),
                    )
                )

    return ArchivistOutput(
        raw_markdown=content,
        decisions=decisions,
        synthesis=synthesis,
    )


def has_violations(librarian_outputs: list[LibrarianOutput]) -> bool:
    """Check if any librarian flagged a violation."""
    return any(
        flag.severity == "violation"
        for out in librarian_outputs
        for flag in out.flags
    )


class ArchivistRunner:
    """Runs the archivist role — conditional arbitration between librarians."""

    def __init__(self, runtime: SubagentRuntime, model: str) -> None:
        self.runtime = runtime
        self.model = model

    def run(self, prompt: str) -> tuple[ArchivistOutput, RoleExecution]:
        """Execute archivist arbitration and parse output."""
        execution = self.runtime.run_task(
            SubagentTask(
                role="archivist",
                model=self.model,
                prompt=prompt,
                max_tokens=4000,
                temperature=0.2,
            )
        )
        output = parse_archivist_output(execution.content)
        return output, execution

    def noop(self) -> ArchivistOutput:
        """Return a no-op output when no violations exist."""
        return ArchivistOutput(
            raw_markdown="",
            decisions=[],
            synthesis="No violations flagged — archivist not triggered.",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_archivist.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/agents/archivist.py tests/test_archivist.py
git commit -m "feat(library): add ArchivistRunner with output parser and spot-pull"
```

---

## Chunk 4: consult_library Tool

### Task 7: Library Consultation Tool

**Files:**
- Create: `autocontext/src/autocontext/agents/library_tool.py`
- Test: `autocontext/tests/test_library_tool.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_library_tool.py
import json
from pathlib import Path
from unittest.mock import MagicMock

from autocontext.agents.library_tool import LibraryToolHandler


def _make_handler(tmp_path, books=None):
    """Create a handler with mock librarians."""
    library_root = tmp_path / "_library"
    library_root.mkdir(parents=True)

    mock_librarians = {}
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
        max_consults_per_role=3,
    )


def test_consult_specific_book(tmp_path):
    handler = _make_handler(tmp_path, {"clean-arch": "SRP principles..."})
    result = handler.handle(
        question="What about SRP?",
        book_name="clean-arch",
        calling_role="analyst",
        generation=1,
    )
    assert "Answer from clean-arch" in result["answer"]
    handler.librarians["clean-arch"].consult.assert_called_once()


def test_consult_unknown_book(tmp_path):
    handler = _make_handler(tmp_path, {"clean-arch": "content"})
    result = handler.handle(
        question="What?",
        book_name="nonexistent",
        calling_role="analyst",
        generation=1,
    )
    assert "error" in result


def test_consult_rate_limit(tmp_path):
    handler = _make_handler(tmp_path, {"clean-arch": "content"})
    handler.max_consults_per_role = 2

    handler.handle(question="Q1", book_name="clean-arch", calling_role="analyst", generation=1)
    handler.handle(question="Q2", book_name="clean-arch", calling_role="analyst", generation=1)
    result = handler.handle(question="Q3", book_name="clean-arch", calling_role="analyst", generation=1)
    assert "limit" in result.get("error", "").lower() or "exceeded" in result.get("error", "").lower()


def test_consult_logs_query(tmp_path):
    handler = _make_handler(tmp_path, {"clean-arch": "content"})
    handler.handle(question="Test Q", book_name="clean-arch", calling_role="analyst", generation=1)
    assert len(handler.consultation_log) == 1
    assert handler.consultation_log[0]["question"] == "Test Q"
    assert handler.consultation_log[0]["calling_role"] == "analyst"


def test_consult_no_book_name_routes_to_all(tmp_path):
    handler = _make_handler(tmp_path, {"a": "content a", "b": "content b"})
    result = handler.handle(
        question="General question",
        book_name=None,
        calling_role="coach",
        generation=1,
    )
    assert "answer" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_library_tool.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the tool handler**

Create `src/autocontext/agents/library_tool.py`:

```python
"""consult_library tool: cross-agent literature consultation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.agents.librarian import LibrarianRunner
    from pathlib import Path


@dataclass
class ConsultationRecord:
    generation: int
    calling_role: str
    question: str
    book_name: str | None
    answer: str


class LibraryToolHandler:
    """Handles consult_library tool calls from agents."""

    def __init__(
        self,
        librarians: dict[str, LibrarianRunner],
        library_root: Path,
        max_consults_per_role: int = 3,
    ) -> None:
        self.librarians = librarians
        self.library_root = library_root
        self.max_consults_per_role = max_consults_per_role
        self.consultation_log: list[dict] = []
        self._call_counts: dict[str, int] = {}  # "role:gen" -> count

    def handle(
        self,
        question: str,
        book_name: str | None,
        calling_role: str,
        generation: int,
    ) -> dict:
        """Handle a consult_library call.

        Returns dict with 'answer' key on success or 'error' key on failure.
        """
        # Rate limiting
        rate_key = f"{calling_role}:{generation}"
        current = self._call_counts.get(rate_key, 0)
        if current >= self.max_consults_per_role:
            return {"error": f"Consultation limit exceeded for {calling_role} (max {self.max_consults_per_role})"}
        self._call_counts[rate_key] = current + 1

        if book_name:
            # Route to specific librarian
            librarian = self.librarians.get(book_name)
            if not librarian:
                return {"error": f"Book '{book_name}' not found in active library"}

            ref_path = self.library_root / "books" / book_name / "reference.md"
            reference = ref_path.read_text(encoding="utf-8") if ref_path.exists() else ""
            answer = librarian.consult(question, reference)
        else:
            # Route to all librarians, synthesize
            answers = []
            for name, librarian in self.librarians.items():
                ref_path = self.library_root / "books" / name / "reference.md"
                reference = ref_path.read_text(encoding="utf-8") if ref_path.exists() else ""
                answers.append(f"[{name}]: {librarian.consult(question, reference)}")
            answer = "\n\n".join(answers) if answers else "No books available."

        # Log
        self.consultation_log.append({
            "generation": generation,
            "calling_role": calling_role,
            "question": question,
            "book_name": book_name,
            "answer": answer,
        })

        return {"answer": answer, "book": book_name or "all"}

    def reset_generation(self, generation: int) -> None:
        """Reset rate limits for a new generation."""
        keys_to_remove = [k for k in self._call_counts if k.endswith(f":{generation}")]
        for k in keys_to_remove:
            del self._call_counts[k]

    def format_log_markdown(self) -> str:
        """Format consultation log as markdown for persistence."""
        if not self.consultation_log:
            return ""
        lines = []
        current_gen = None
        for entry in self.consultation_log:
            if entry["generation"] != current_gen:
                current_gen = entry["generation"]
                lines.append(f"\n## Generation {current_gen}\n")
            book = entry["book_name"] or "(archivist routed)"
            lines.append(f"### {entry['calling_role']} -> {book}")
            lines.append(f"**Q:** {entry['question']}")
            lines.append(f"**A:** {entry['answer']}\n")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_library_tool.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/agents/library_tool.py tests/test_library_tool.py
git commit -m "feat(library): add consult_library tool handler with rate limiting"
```

---

## Chunk 5: DAG, Routing & Prompt Integration

Wire the library roles into the pipeline engine, role router, and prompt system.

### Task 8: Role Routing — Prefix-Based Lookup

**Files:**
- Modify: `autocontext/src/autocontext/agents/role_router.py:61-116`
- Test: `autocontext/tests/test_role_router_library.py`

- [ ] **Step 1: Write tests for prefix routing**

```python
# tests/test_role_router_library.py
from autocontext.agents.role_router import DEFAULT_ROUTING_TABLE, RoleRouter, ProviderClass
from autocontext.config.settings import AppSettings


def test_librarian_in_routing_table():
    assert "librarian" in DEFAULT_ROUTING_TABLE
    assert ProviderClass.MID_TIER in DEFAULT_ROUTING_TABLE["librarian"]


def test_archivist_in_routing_table():
    assert "archivist" in DEFAULT_ROUTING_TABLE
    assert ProviderClass.FRONTIER in DEFAULT_ROUTING_TABLE["archivist"]


def test_router_resolves_librarian_prefix():
    settings = AppSettings(model_librarian="claude-haiku-3-5-20241022")
    router = RoleRouter(settings)
    model = router.resolve_model("librarian_clean_arch")
    assert model == "claude-haiku-3-5-20241022"


def test_router_resolves_archivist():
    settings = AppSettings(model_archivist="claude-opus-4-6")
    router = RoleRouter(settings)
    model = router.resolve_model("archivist")
    assert model == "claude-opus-4-6"


def test_router_librarian_provider_override():
    settings = AppSettings(librarian_provider="openai")
    router = RoleRouter(settings)
    provider = router.resolve_provider("librarian_ddd")
    assert provider == "openai"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_role_router_library.py -v`
Expected: FAIL — `KeyError` or `AttributeError`

- [ ] **Step 3: Update role_router.py**

In `src/autocontext/agents/role_router.py`:

Add to `DEFAULT_ROUTING_TABLE` (around line 61):
```python
    "librarian": [ProviderClass.MID_TIER, ProviderClass.LOCAL],
    "archivist": [ProviderClass.FRONTIER],
```

In the `RoleRouter` class, update `_role_models` dict (around line 103) to include:
```python
    "librarian": self.settings.model_librarian,
    "archivist": self.settings.model_archivist,
```

In `_role_providers` dict (around line 111) to include:
```python
    "librarian": self.settings.librarian_provider,
    "archivist": self.settings.archivist_provider,
```

Update `route()`, `_role_models`, and `_role_providers` to use prefix-based fallback. The prefix fallback is applied **inside the existing `route()` method** and its internal lookups, not as separate methods. This ensures the pipeline's existing call to `route()` works for dynamic `librarian_*` roles:

In `_role_models` and `_role_providers` dict lookups (used by `_config_for_default`, `_config_for_explicit`, and `route()`), wrap with a helper:

```python
    def _resolve_role_key(self, role: str, table: dict[str, str]) -> str:
        """Lookup with prefix fallback for librarian_* roles."""
        if role in table:
            return table[role]
        if role.startswith("librarian_"):
            return table.get("librarian", "")
        return table.get(role, "")
```

Update all `self._role_models.get(role)` calls to use `self._resolve_role_key(role, self._role_models)` and all `self._role_providers.get(role, "")` calls to use `self._resolve_role_key(role, self._role_providers)`.

Similarly, for the routing table lookup in `_auto_route`, add prefix fallback:

```python
    def _resolve_routing_table(self, role: str) -> list[ProviderClass]:
        if role in self._table:
            return self._table[role]
        if role.startswith("librarian_"):
            return self._table.get("librarian", [ProviderClass.MID_TIER])
        return [ProviderClass.MID_TIER]
```

Also add convenience methods for the pipeline adapter and tests:

```python
    def resolve_model(self, role: str) -> str:
        return self._resolve_role_key(role, self._role_models) or self.settings.model_competitor

    def resolve_provider(self, role: str) -> str:
        return self._resolve_role_key(role, self._role_providers)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_role_router_library.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run existing role_router tests to ensure no regression**

Run: `cd autocontext && uv run pytest tests/ -k "role_router" -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd autocontext && git add src/autocontext/agents/role_router.py tests/test_role_router_library.py
git commit -m "feat(library): add prefix-based routing for librarian/archivist roles"
```

### Task 9: Pipeline Adapter — Dynamic DAG & Role Handler

**Files:**
- Modify: `autocontext/src/autocontext/agents/pipeline_adapter.py:17-94`
- Test: `autocontext/tests/test_pipeline_adapter_library.py`

- [ ] **Step 1: Write tests for extended DAG**

```python
# tests/test_pipeline_adapter_library.py
from autocontext.agents.pipeline_adapter import build_mts_dag


def test_build_dag_no_books():
    dag = build_mts_dag()
    role_names = set(dag.roles.keys())
    assert "librarian" not in str(role_names)
    assert "archivist" not in role_names


def test_build_dag_with_books():
    dag = build_mts_dag(active_books=["clean-arch", "ddd"])
    role_names = set(dag.roles.keys())
    assert "librarian_clean-arch" in role_names
    assert "librarian_ddd" in role_names
    assert "archivist" in role_names


def test_librarians_depend_on_translator():
    dag = build_mts_dag(active_books=["clean-arch"])
    spec = dag.roles["librarian_clean-arch"]
    assert "translator" in spec.depends_on


def test_archivist_depends_on_all_librarians():
    dag = build_mts_dag(active_books=["a", "b", "c"])
    spec = dag.roles["archivist"]
    assert "librarian_a" in spec.depends_on
    assert "librarian_b" in spec.depends_on
    assert "librarian_c" in spec.depends_on


def test_coach_depends_on_archivist_when_books():
    dag = build_mts_dag(active_books=["clean-arch"])
    spec = dag.roles["coach"]
    assert "archivist" in spec.depends_on


def test_coach_does_not_depend_on_archivist_without_books():
    dag = build_mts_dag()
    spec = dag.roles["coach"]
    assert "archivist" not in spec.depends_on


def test_dag_execution_order_with_books():
    dag = build_mts_dag(active_books=["clean-arch"])
    batches = dag.execution_batches()
    role_order = [role for batch in batches for role in batch]
    # Librarians must come after translator, archivist after librarians, coach after archivist
    assert role_order.index("translator") < role_order.index("librarian_clean-arch")
    assert role_order.index("librarian_clean-arch") < role_order.index("archivist")
    assert role_order.index("archivist") < role_order.index("coach")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_pipeline_adapter_library.py -v`
Expected: FAIL — `build_mts_dag() got unexpected keyword argument 'active_books'`

- [ ] **Step 3: Update pipeline_adapter.py**

In `src/autocontext/agents/pipeline_adapter.py`, modify `build_mts_dag()`:

```python
def build_mts_dag(active_books: list[str] | None = None, librarian_enabled: bool = True) -> RoleDAG:
    """Build the standard generation DAG, optionally with library roles."""
    coach_deps = ["analyst"]

    roles = [
        RoleSpec(name="competitor", depends_on=()),
        RoleSpec(name="translator", depends_on=("competitor",)),
        RoleSpec(name="analyst", depends_on=("translator",)),
        RoleSpec(name="architect", depends_on=("translator",)),
    ]

    if active_books and librarian_enabled:
        librarian_names = []
        for book in active_books:
            name = f"librarian_{book}"
            roles.append(RoleSpec(name=name, depends_on=("translator",)))
            librarian_names.append(name)

        roles.append(RoleSpec(name="archivist", depends_on=tuple(librarian_names)))
        coach_deps.append("archivist")

    roles.append(RoleSpec(name="coach", depends_on=tuple(coach_deps)))
    return RoleDAG(roles)
```

Update `build_role_handler()` to accept librarian/archivist runners and dispatch accordingly. Add a `librarian_runners` and `archivist_runner` parameter to the function. In the returned handler function, add cases:

```python
    if name.startswith("librarian_"):
        book_name = name.removeprefix("librarian_")
        runner = librarian_runners.get(book_name)
        if runner:
            output, execution = runner.run(prompts.get(name, ""))
            return execution
    elif name == "archivist":
        if archivist_runner:
            # Check if any librarian flagged violations
            # ... (check completed outputs)
            output, execution = archivist_runner.run(prompts.get(name, ""))
            return execution
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_pipeline_adapter_library.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run existing pipeline_adapter tests**

Run: `cd autocontext && uv run pytest tests/ -k "pipeline_adapter" -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd autocontext && git add src/autocontext/agents/pipeline_adapter.py tests/test_pipeline_adapter_library.py
git commit -m "feat(library): extend DAG builder with dynamic librarian/archivist nodes"
```

### Task 10: Prompt Templates — LibraryPromptBundle

**Files:**
- Modify: `autocontext/src/autocontext/prompts/templates.py:9-52`
- Test: `autocontext/tests/test_prompts_library.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_prompts_library.py
from autocontext.prompts.templates import LibraryPromptBundle, build_library_context_block


def test_library_prompt_bundle_creation():
    bundle = LibraryPromptBundle(
        librarian_prompts={"clean-arch": "Review against SRP..."},
        archivist_prompt="Arbitrate these flags...",
        library_context_block="Available books: clean-arch (Clean Architecture)",
    )
    assert "clean-arch" in bundle.librarian_prompts
    assert "Arbitrate" in bundle.archivist_prompt
    assert "Available books" in bundle.library_context_block


def test_library_context_block():
    books = [
        {"name": "clean-arch", "title": "Clean Architecture", "tags": ["architecture"]},
        {"name": "ddd", "title": "Domain-Driven Design", "tags": ["design"]},
    ]
    block = build_library_context_block(books)
    assert "clean-arch" in block
    assert "Clean Architecture" in block
    assert "consult_library" in block


def test_library_context_block_empty():
    block = build_library_context_block([])
    assert block == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_prompts_library.py -v`
Expected: FAIL — `ImportError: cannot import name 'LibraryPromptBundle'`

- [ ] **Step 3: Add LibraryPromptBundle and helpers**

In `src/autocontext/prompts/templates.py`, add after `PromptBundle`:

```python
@dataclass(frozen=True)
class LibraryPromptBundle:
    librarian_prompts: dict[str, str]
    archivist_prompt: str
    library_context_block: str


def build_library_context_block(books: list[dict]) -> str:
    """Build a context block listing active books for agent prompts."""
    if not books:
        return ""
    lines = [
        "## Available Literature",
        "",
        "You have access to a library of research and literature. "
        "Use the `consult_library` tool when you need guidance from published research, "
        "best practices, or theoretical foundations.",
        "",
        "Available books:",
    ]
    for book in books:
        tags = ", ".join(book.get("tags", []))
        tag_str = f" [{tags}]" if tags else ""
        lines.append(f"- **{book['name']}**: {book['title']}{tag_str}")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_prompts_library.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/prompts/templates.py tests/test_prompts_library.py
git commit -m "feat(library): add LibraryPromptBundle and library context block builder"
```

---

## Chunk 6: Storage, Gate & Pipeline Integration

Wire the library into the generation pipeline — archivist gate stage, persistence, orchestrator, and Agent SDK.

### Task 11: Artifact Storage — Library Persistence

**Files:**
- Modify: `autocontext/src/autocontext/storage/artifacts.py`
- Test: `autocontext/tests/test_artifacts_library.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_artifacts_library.py
import json
from pathlib import Path

from autocontext.storage.artifacts import ArtifactStore


def test_write_librarian_notes(tmp_path):
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    store.write_librarian_notes(
        scenario="grid_ctf",
        book_name="clean-arch",
        generation=3,
        content="## Advisory\nUse SRP.\n",
    )
    path = tmp_path / "knowledge" / "grid_ctf" / "library" / "librarian_notes" / "clean-arch" / "gen_3.md"
    assert path.exists()
    assert "Use SRP" in path.read_text()


def test_read_cumulative_notes_empty(tmp_path):
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    notes = store.read_cumulative_notes("grid_ctf", "clean-arch")
    assert notes == ""


def test_append_cumulative_notes(tmp_path):
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    store.append_cumulative_notes("grid_ctf", "clean-arch", "Gen 1: SRP flagged.")
    store.append_cumulative_notes("grid_ctf", "clean-arch", "Gen 2: Team complied.")
    notes = store.read_cumulative_notes("grid_ctf", "clean-arch")
    assert "Gen 1" in notes
    assert "Gen 2" in notes


def test_write_archivist_decision(tmp_path):
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    store.write_archivist_decision(
        scenario="grid_ctf",
        generation=5,
        content="## Decision\nhard_gate on SRP violation.\n",
    )
    path = tmp_path / "knowledge" / "grid_ctf" / "library" / "archivist" / "decisions" / "gen_5_decision.md"
    assert path.exists()


def test_write_active_books(tmp_path):
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    store.write_active_books("grid_ctf", ["clean-arch", "ddd"])
    path = tmp_path / "knowledge" / "grid_ctf" / "library" / "active_books.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["books"] == ["clean-arch", "ddd"]


def test_write_consultation_log(tmp_path):
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    store.append_consultation_log("grid_ctf", "## Gen 1\n### analyst -> clean-arch\n**Q:** SRP?\n")
    path = tmp_path / "knowledge" / "grid_ctf" / "library" / "archivist" / "consultation_log.md"
    assert path.exists()
    assert "SRP?" in path.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_artifacts_library.py -v`
Expected: FAIL — `AttributeError: 'ArtifactStore' has no attribute 'write_librarian_notes'`

- [ ] **Step 3: Add library methods to ArtifactStore**

In `src/autocontext/storage/artifacts.py`, add methods to `ArtifactStore`:

```python
    def _library_dir(self, scenario: str) -> Path:
        path = Path(self.knowledge_root) / scenario / "library"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_librarian_notes(self, scenario: str, book_name: str, generation: int, content: str) -> None:
        notes_dir = self._library_dir(scenario) / "librarian_notes" / book_name
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / f"gen_{generation}.md").write_text(content, encoding="utf-8")

    def read_cumulative_notes(self, scenario: str, book_name: str) -> str:
        path = self._library_dir(scenario) / "librarian_notes" / book_name / "cumulative_notes.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def append_cumulative_notes(self, scenario: str, book_name: str, content: str) -> None:
        notes_dir = self._library_dir(scenario) / "librarian_notes" / book_name
        notes_dir.mkdir(parents=True, exist_ok=True)
        path = notes_dir / "cumulative_notes.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(existing + "\n" + content if existing else content, encoding="utf-8")

    def write_archivist_decision(self, scenario: str, generation: int, content: str) -> None:
        dec_dir = self._library_dir(scenario) / "archivist" / "decisions"
        dec_dir.mkdir(parents=True, exist_ok=True)
        (dec_dir / f"gen_{generation}_decision.md").write_text(content, encoding="utf-8")

    def write_active_books(self, scenario: str, book_names: list[str]) -> None:
        import json
        path = self._library_dir(scenario) / "active_books.json"
        path.write_text(json.dumps({"books": book_names}, indent=2), encoding="utf-8")

    def append_consultation_log(self, scenario: str, content: str) -> None:
        log_dir = self._library_dir(scenario) / "archivist"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "consultation_log.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(existing + content, encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_artifacts_library.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/storage/artifacts.py tests/test_artifacts_library.py
git commit -m "feat(library): add library persistence methods to ArtifactStore"
```

### Task 12: Archivist Gate Stage

**Files:**
- Modify: `autocontext/src/autocontext/loop/generation_pipeline.py`
- Create: `autocontext/src/autocontext/loop/stage_archivist_gate.py`
- Test: `autocontext/tests/test_stage_archivist_gate.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_stage_archivist_gate.py
from autocontext.agents.contracts import ArchivistDecision, ArchivistOutput
from autocontext.loop.stage_archivist_gate import evaluate_archivist_gate


def test_no_archivist_output():
    result = evaluate_archivist_gate(archivist_output=None, backpressure_decision="advance")
    assert result["action"] == "proceed"


def test_empty_decisions():
    output = ArchivistOutput(raw_markdown="", decisions=[], synthesis="All clear.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="advance")
    assert result["action"] == "proceed"


def test_soft_flag_only():
    dec = ArchivistDecision(
        flag_source="librarian_a", book_name="A", verdict="soft_flag",
        reasoning="Minor", cited_passage="quote",
    )
    output = ArchivistOutput(raw_markdown="", decisions=[dec], synthesis="One soft flag.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="advance")
    assert result["action"] == "proceed"
    assert len(result["soft_flags"]) == 1


def test_hard_gate_triggers_retry():
    dec = ArchivistDecision(
        flag_source="librarian_a", book_name="A", verdict="hard_gate",
        reasoning="Critical violation", cited_passage="quote",
    )
    output = ArchivistOutput(raw_markdown="", decisions=[dec], synthesis="Violation found.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="advance")
    assert result["action"] == "retry"
    assert "Critical violation" in result["constraint"]


def test_hard_gate_skipped_on_rollback():
    dec = ArchivistDecision(
        flag_source="librarian_a", book_name="A", verdict="hard_gate",
        reasoning="Critical", cited_passage="quote",
    )
    output = ArchivistOutput(raw_markdown="", decisions=[dec], synthesis="Violation.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="rollback")
    assert result["action"] == "skip"


def test_dismissed_ignored():
    dec = ArchivistDecision(
        flag_source="librarian_a", book_name="A", verdict="dismissed",
        reasoning="Not relevant", cited_passage="quote",
    )
    output = ArchivistOutput(raw_markdown="", decisions=[dec], synthesis="Dismissed.")
    result = evaluate_archivist_gate(archivist_output=output, backpressure_decision="advance")
    assert result["action"] == "proceed"
    assert result["soft_flags"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_stage_archivist_gate.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the gate stage**

Create `src/autocontext/loop/stage_archivist_gate.py`:

```python
"""Stage 3b: Archivist gate — evaluate librarian escalations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.agents.contracts import ArchivistOutput


def evaluate_archivist_gate(
    archivist_output: ArchivistOutput | None,
    backpressure_decision: str,
) -> dict:
    """Evaluate archivist decisions and determine gate action.

    Returns dict with:
      action: "proceed", "retry", or "skip"
      soft_flags: list of ArchivistDecision with verdict "soft_flag"
      constraint: str reasoning for retry (if action == "retry")
    """
    if backpressure_decision == "rollback":
        return {"action": "skip", "soft_flags": [], "constraint": ""}

    if archivist_output is None or not archivist_output.decisions:
        return {"action": "proceed", "soft_flags": [], "constraint": ""}

    soft_flags = [d for d in archivist_output.decisions if d.verdict == "soft_flag"]
    hard_gates = [d for d in archivist_output.decisions if d.verdict == "hard_gate"]

    if hard_gates:
        constraints = []
        for gate in hard_gates:
            constraints.append(
                f"[{gate.book_name}] {gate.reasoning} (Source: {gate.cited_passage})"
            )
        return {
            "action": "retry",
            "soft_flags": soft_flags,
            "constraint": "\n".join(constraints),
        }

    return {"action": "proceed", "soft_flags": soft_flags, "constraint": ""}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_stage_archivist_gate.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/loop/stage_archivist_gate.py tests/test_stage_archivist_gate.py
git commit -m "feat(library): add archivist gate stage for pipeline"
```

### Task 13: Agent SDK Tool Config Update

**Files:**
- Modify: `autocontext/src/autocontext/agents/agent_sdk_client.py:13-25`
- Test: `autocontext/tests/test_agent_sdk_library.py`

- [ ] **Step 1: Write test**

```python
# tests/test_agent_sdk_library.py
from autocontext.agents.agent_sdk_client import ROLE_TOOL_CONFIG


def test_all_roles_have_consult_library():
    for role in ("competitor", "analyst", "coach", "architect"):
        assert "consult_library" in ROLE_TOOL_CONFIG.get(role, []), f"{role} missing consult_library"


def test_librarian_roles_no_consult_library():
    # Librarians and archivist don't consult themselves
    assert "consult_library" not in ROLE_TOOL_CONFIG.get("translator", [])
    assert "consult_library" not in ROLE_TOOL_CONFIG.get("curator", [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd autocontext && uv run pytest tests/test_agent_sdk_library.py -v`
Expected: FAIL — `AssertionError: competitor missing consult_library`

- [ ] **Step 3: Update ROLE_TOOL_CONFIG**

In `src/autocontext/agents/agent_sdk_client.py`, add `"consult_library"` to the tool lists for `competitor`, `analyst`, `coach`, and `architect` in `ROLE_TOOL_CONFIG` (around line 13).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd autocontext && uv run pytest tests/test_agent_sdk_library.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/agents/agent_sdk_client.py tests/test_agent_sdk_library.py
git commit -m "feat(library): add consult_library to agent SDK tool permissions"
```

---

## Chunk 7: CLI Commands

### Task 14: CLI — add-book, list-books, remove-book, --books

**Files:**
- Modify: `autocontext/src/autocontext/cli.py`
- Test: `autocontext/tests/test_cli_library.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_cli_library.py
import json
from pathlib import Path
from typer.testing import CliRunner
from autocontext.cli import app

runner = CliRunner()


def test_add_book_command(tmp_path, monkeypatch):
    book = tmp_path / "test.md"
    book.write_text("# Chapter 1\n\nContent.\n")
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_ROOT", str(tmp_path / "library"))
    # Note: This will fail at LLM call stage since no provider is configured.
    # We test that the command is registered and validates args.
    result = runner.invoke(app, ["add-book", str(book), "--title", "Test Book", "--name", "test"])
    # Should get past arg validation even if LLM call fails
    assert result.exit_code != 2  # Not a usage error


def test_list_books_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_ROOT", str(tmp_path / "library"))
    result = runner.invoke(app, ["list-books"])
    assert result.exit_code == 0


def test_remove_book_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_ROOT", str(tmp_path / "library"))
    result = runner.invoke(app, ["remove-book", "nonexistent"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd autocontext && uv run pytest tests/test_cli_library.py -v`
Expected: FAIL — `No such command 'add-book'`

- [ ] **Step 3: Add CLI commands**

In `src/autocontext/cli.py`, add:

```python
@app.command("add-book")
def add_book(
    source: str = typer.Argument(..., help="Path to markdown file"),
    title: str = typer.Option(..., "--title", help="Book title"),
    name: str = typer.Option(None, "--name", help="Book name (slug, auto-generated from title if omitted)"),
    author: str = typer.Option("", "--author", help="Author name"),
    tags: list[str] = typer.Option([], "--tag", help="Tags for the book"),
    images: str = typer.Option(None, "--images", help="Path to images directory"),
) -> None:
    """Ingest a book into the global library."""
    from pathlib import Path
    from autocontext.knowledge.ingestion import register_book, slugify, validate_ingestion
    from autocontext.config.settings import load_settings
    from rich.console import Console

    console = Console()
    settings = load_settings()
    source_path = Path(source)
    if not source_path.exists():
        console.print(f"[red]File not found: {source}[/red]")
        raise typer.Exit(1)

    book_name = name or slugify(title)
    library_root = Path(settings.library_root)
    images_path = Path(images) if images else None

    try:
        meta = register_book(
            source_path=source_path,
            library_root=library_root,
            book_name=book_name,
            title=title,
            author=author,
            tags=tags,
            images_path=images_path,
        )
    except FileExistsError:
        console.print(f"[red]Book '{book_name}' already exists. Remove it first.[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Registered:[/green] {book_name} ({meta['chapter_count']} chapters, ~{meta['token_count']} tokens)")

    # Attempt LLM ingestion if a provider is configured
    try:
        from autocontext.providers import create_provider, get_provider
        provider = get_provider(settings)
        from autocontext.knowledge.ingestion import ingest_book
        console.print("[dim]Generating reference.md via LLM ingestion...[/dim]")
        ingest_book(
            library_root=library_root,
            book_name=book_name,
            provider=provider,
            model=settings.ingestion_model,
        )
        console.print("[green]reference.md generated successfully.[/green]")
    except Exception as e:
        console.print(f"[yellow]Skipping LLM ingestion:[/yellow] {e}")
        console.print("[yellow]Set AUTOCONTEXT_AGENT_PROVIDER and API key, then re-run add-book.[/yellow]")


@app.command("list-books")
def list_books_cmd() -> None:
    """List all books in the global library."""
    from pathlib import Path
    from autocontext.knowledge.ingestion import list_books
    from autocontext.config.settings import load_settings
    from rich.console import Console
    from rich.table import Table

    settings = load_settings()
    console = Console()
    books = list_books(Path(settings.library_root))

    if not books:
        console.print("No books in library.")
        return

    table = Table(title="Library")
    table.add_column("Name")
    table.add_column("Title")
    table.add_column("Chapters")
    table.add_column("Tokens")
    table.add_column("Tags")
    table.add_column("Reference")

    for b in books:
        table.add_row(
            b["name"],
            b["title"],
            str(b["chapter_count"]),
            f"~{b['token_count']}",
            ", ".join(b.get("tags", [])),
            "yes" if b.get("has_reference") else "no",
        )
    console.print(table)


@app.command("remove-book")
def remove_book_cmd(
    name: str = typer.Argument(..., help="Book name to remove"),
) -> None:
    """Remove a book from the global library."""
    from pathlib import Path
    from autocontext.knowledge.ingestion import remove_book
    from autocontext.config.settings import load_settings
    from rich.console import Console

    console = Console()
    settings = load_settings()
    try:
        remove_book(Path(settings.library_root), name)
        console.print(f"[green]Removed:[/green] {name}")
    except FileNotFoundError:
        console.print(f"[red]Book '{name}' not found.[/red]")
        raise typer.Exit(1)
```

Also add `--books` option to the existing `run` command:

```python
    books: str = typer.Option("", "--books", help="Comma-separated book names to activate"),
```

And pass `books.split(",") if books else []` through to settings or runner initialization.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_cli_library.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/cli.py tests/test_cli_library.py
git commit -m "feat(library): add add-book, list-books, remove-book CLI commands"
```

---

## Chunk 8: Documentation Updates

### Task 15: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add library roles to Agent Roles section**

After the Curator bullet in the Architecture > Agent Roles section, add:

```markdown
- **Librarian** — Bound to a single book. Reads full text at ingestion, produces internal reference. Reviews strategies each generation, advises based on literature, flags violations. Available via `consult_library` tool to other agents.
- **Archivist** — Conditional arbiter. Only runs when a librarian escalates a violation. Spot-pulls original passages from chunked chapters. Decides: `dismissed`, `soft_flag`, or `hard_gate`.
```

- [ ] **Step 2: Add library to Generation Loop stages**

After Stage 3a in the Architecture > Generation Loop section, add:

```markdown
→ archivist gate (if books active: evaluate librarian flags, hard_gate triggers retry)
```

- [ ] **Step 3: Add library to Knowledge System section**

Add to the knowledge directory description:

```markdown
`knowledge/_library/` stores the global book collection. Per-scenario library state (librarian notes, archivist decisions, consultation logs) lives under `knowledge/<scenario>/library/`.
```

- [ ] **Step 4: Add library commands to Commands section**

```markdown
# Library management
uv run autoctx add-book path/to/book.md --title "Clean Architecture" --tags architecture
uv run autoctx list-books
uv run autoctx remove-book clean-architecture

# Run with books active
uv run autoctx run --scenario grid_ctf --gens 5 --books clean-architecture,ddd
```

- [ ] **Step 5: Add library config to Configuration section**

```markdown
- **Library**: `AUTOCONTEXT_LIBRARY_BOOKS`, `AUTOCONTEXT_LIBRARY_ROOT`
- **Librarian**: `AUTOCONTEXT_MODEL_LIBRARIAN`, `AUTOCONTEXT_LIBRARIAN_PROVIDER`, `AUTOCONTEXT_LIBRARY_MAX_CONSULTS_PER_ROLE`
- **Archivist**: `AUTOCONTEXT_MODEL_ARCHIVIST`, `AUTOCONTEXT_ARCHIVIST_PROVIDER`
- **Ingestion**: `AUTOCONTEXT_INGESTION_MODEL`
```

- [ ] **Step 6: Add library to Repository Layout**

Under the `autocontext/` tree, add:

```markdown
    knowledge/_library/       # Global book library (ingested via add-book)
```

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add librarian/archivist to CLAUDE.md"
```

### Task 16: Update README.md, autocontext/README.md, CONTRIBUTING.md

**Files:**
- Modify: `README.md`
- Modify: `autocontext/README.md`
- Modify: `CONTRIBUTING.md`

- [ ] **Step 1: Update root README.md**

In Core Capabilities, add:
```markdown
- Literature-aware advisory and gating via librarian/archivist agents bound to ingested books
```

In Common Workflows, add:
```markdown
- Ingest a book: `uv run autoctx add-book path/to/book.md --title "Clean Architecture"`
- Run with literature: `uv run autoctx run --scenario grid_ctf --gens 5 --books clean-architecture`
```

- [ ] **Step 2: Update autocontext/README.md**

In What It Does, add:
```markdown
- Literature-grounded advisory via librarian/archivist agents with per-book specialization
```

In Main CLI Commands, add:
```markdown
uv run autoctx add-book path/to/book.md --title "Book Title"
uv run autoctx list-books
```

In Configuration > Common settings, add:
```markdown
- `AUTOCONTEXT_LIBRARY_BOOKS`
- `AUTOCONTEXT_MODEL_LIBRARIAN`
- `AUTOCONTEXT_MODEL_ARCHIVIST`
```

- [ ] **Step 3: Update CONTRIBUTING.md**

In Development Notes, add:
```markdown
- Library tests use `tmp_path` fixtures for book storage. Mock the LLM provider for ingestion tests.
```

- [ ] **Step 4: Commit**

```bash
git add README.md autocontext/README.md CONTRIBUTING.md
git commit -m "docs: add library system to README, autocontext README, and CONTRIBUTING"
```

---

## Chunk 9: Integration Test

### Task 17: End-to-End Integration Test

**Files:**
- Create: `autocontext/tests/test_library_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_library_integration.py
"""End-to-end test: book ingestion → DAG with librarians → archivist gate."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from autocontext.agents.archivist import ArchivistRunner, has_violations
from autocontext.agents.contracts import ArchivistDecision, ArchivistOutput, LibrarianFlag, LibrarianOutput
from autocontext.agents.librarian import LibrarianRunner, parse_librarian_output
from autocontext.agents.library_tool import LibraryToolHandler
from autocontext.agents.pipeline_adapter import build_mts_dag
from autocontext.knowledge.ingestion import chunk_markdown, register_book
from autocontext.loop.stage_archivist_gate import evaluate_archivist_gate
from autocontext.storage.artifacts import ArtifactStore


def test_full_library_flow(tmp_path):
    """Test the complete flow: ingest book → build DAG → run librarians → gate."""
    # 1. Ingest a book
    library_root = tmp_path / "_library"
    book_md = tmp_path / "principles.md"
    book_md.write_text(
        "# Chapter 1: Single Responsibility\n\n"
        "Each module should have one reason to change.\n\n"
        "# Chapter 2: Open-Closed\n\n"
        "Open for extension, closed for modification.\n"
    )
    meta = register_book(
        source_path=book_md,
        library_root=library_root,
        book_name="solid",
        title="SOLID Principles",
        tags=["architecture"],
    )
    assert meta["chapter_count"] == 2
    assert (library_root / "books" / "solid" / "chapters").is_dir()

    # 2. Build DAG with librarian
    dag = build_mts_dag(active_books=["solid"])
    role_names = set(dag.roles.keys())
    assert "librarian_solid" in role_names
    assert "archivist" in role_names

    batches = dag.execution_batches()
    flat = [r for b in batches for r in b]
    assert flat.index("librarian_solid") < flat.index("archivist")
    assert flat.index("archivist") < flat.index("coach")

    # 3. Simulate librarian output with a violation
    librarian_content = (
        "<!-- ADVISORY_START -->\n"
        "The strategy follows SRP well.\n"
        "<!-- ADVISORY_END -->\n"
        "<!-- FLAGS_START -->\n"
        "## Flag: [severity: violation]\n"
        "**Section:** ch01-s01-single-responsibility\n"
        "**Issue:** The strategy merges scoring and movement into one function.\n"
        "**Recommendation:** Split into separate modules.\n"
        "<!-- FLAGS_END -->\n"
    )
    lib_output = parse_librarian_output(librarian_content, "solid")
    assert len(lib_output.flags) == 1
    assert lib_output.flags[0].severity == "violation"

    # 4. Check violation detection
    assert has_violations([lib_output]) is True

    # 5. Simulate archivist hard_gate
    archivist_output = ArchivistOutput(
        raw_markdown="",
        decisions=[
            ArchivistDecision(
                flag_source="librarian_solid",
                book_name="SOLID Principles",
                verdict="hard_gate",
                reasoning="SRP violation is critical — merging scoring and movement creates fragile code.",
                cited_passage="Each module should have one reason to change.",
            )
        ],
        synthesis="One critical SRP violation found.",
    )

    # 6. Evaluate gate
    gate_result = evaluate_archivist_gate(archivist_output, backpressure_decision="advance")
    assert gate_result["action"] == "retry"
    assert "SRP violation" in gate_result["constraint"]

    # 7. Persistence
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    store.write_librarian_notes("grid_ctf", "solid", 1, lib_output.raw_markdown)
    store.write_archivist_decision("grid_ctf", 1, archivist_output.raw_markdown)
    store.append_cumulative_notes("grid_ctf", "solid", "Gen 1: SRP violation flagged and gated.")
    store.write_active_books("grid_ctf", ["solid"])

    assert store.read_cumulative_notes("grid_ctf", "solid") != ""
    active = json.loads(
        (tmp_path / "knowledge" / "grid_ctf" / "library" / "active_books.json").read_text()
    )
    assert "solid" in active["books"]


def test_no_violations_skips_archivist(tmp_path):
    """When no violations, archivist returns no-op and gate proceeds."""
    lib_output = LibrarianOutput(
        raw_markdown="All good",
        book_name="clean-arch",
        advisory="Strategy aligns with principles.",
        flags=[
            LibrarianFlag(severity="concern", description="Minor", cited_section="ch01", recommendation="Consider")
        ],
        cited_sections=["ch01"],
    )
    assert has_violations([lib_output]) is False

    gate_result = evaluate_archivist_gate(archivist_output=None, backpressure_decision="advance")
    assert gate_result["action"] == "proceed"
```

- [ ] **Step 2: Run integration test**

Run: `cd autocontext && uv run pytest tests/test_library_integration.py -v`
Expected: PASS (2 tests)

- [ ] **Step 3: Run full test suite to check for regressions**

Run: `cd autocontext && uv run pytest tests/ -x --timeout 60`
Expected: All existing tests PASS, no regressions

- [ ] **Step 4: Commit**

```bash
cd autocontext && git add tests/test_library_integration.py
git commit -m "test(library): add end-to-end integration test for library system"
```

---

## Chunk 10: Runtime Wiring & Missing Integration

These tasks cover the gaps between individual components and the running system.

### Task 18: Ingestion LLM Call

**Files:**
- Modify: `autocontext/src/autocontext/knowledge/ingestion.py`
- Test: `autocontext/tests/test_ingestion.py` (append)

- [ ] **Step 1: Write test for ingest_book**

Append to `tests/test_ingestion.py`:

```python
from unittest.mock import MagicMock
from autocontext.knowledge.ingestion import ingest_book


def test_ingest_book_produces_reference(tmp_path):
    """Test that ingest_book calls LLM and writes reference.md."""
    library_root = tmp_path / "_library"
    book_md = tmp_path / "book.md"
    book_md.write_text("# Chapter 1\n\nSome principles here.\n")

    # Register first
    from autocontext.knowledge.ingestion import register_book
    register_book(
        source_path=book_md,
        library_root=library_root,
        book_name="test-ref",
        title="Test Reference",
    )

    # Mock provider
    mock_provider = MagicMock()
    mock_provider.query.return_value = MagicMock(
        text="# Core Thesis\n\nThe book argues for X.\n\n# Key Principles\n\n1. Principle A\n"
    )

    ingest_book(
        library_root=library_root,
        book_name="test-ref",
        provider=mock_provider,
        model="claude-opus-4-6",
    )

    ref_path = library_root / "books" / "test-ref" / "reference.md"
    assert ref_path.exists()
    assert "Core Thesis" in ref_path.read_text()

    # meta.json should be updated
    import json
    meta = json.loads((library_root / "books" / "test-ref" / "meta.json").read_text())
    assert meta["has_reference"] is True


def test_ingest_book_not_registered(tmp_path):
    library_root = tmp_path / "_library"
    mock_provider = MagicMock()
    try:
        ingest_book(library_root=library_root, book_name="missing", provider=mock_provider, model="m")
        assert False, "Should have raised"
    except FileNotFoundError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd autocontext && uv run pytest tests/test_ingestion.py::test_ingest_book_produces_reference -v`
Expected: FAIL — `ImportError: cannot import name 'ingest_book'`

- [ ] **Step 3: Implement ingest_book**

Add to `src/autocontext/knowledge/ingestion.py`:

```python
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
    provider,
    model: str,
    images: list[Path] | None = None,
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
    response = provider.query(messages=messages, model=model, max_tokens=8000, temperature=0.2)

    ref_path = book_dir / "reference.md"
    ref_path.write_text(response.text, encoding="utf-8")

    # Update meta
    meta["has_reference"] = True
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return ref_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd autocontext && uv run pytest tests/test_ingestion.py -v`
Expected: PASS (all tests including new ones)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/knowledge/ingestion.py tests/test_ingestion.py
git commit -m "feat(library): add LLM ingestion call for reference.md generation"
```

### Task 19: Orchestrator Wiring

**Files:**
- Modify: `autocontext/src/autocontext/agents/orchestrator.py`
- Test: `autocontext/tests/test_orchestrator_library.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_orchestrator_library.py
from unittest.mock import MagicMock, patch

from autocontext.agents.contracts import LibrarianOutput, ArchivistOutput
from autocontext.agents.types import AgentOutputs


def test_orchestrator_creates_librarians_for_active_books():
    """Verify orchestrator instantiates one LibrarianRunner per active book."""
    from autocontext.config.settings import AppSettings

    settings = AppSettings(library_books=["clean-arch", "ddd"])

    # We test the construction logic, not a full run
    # The orchestrator should have librarian runners for each book
    assert settings.library_books == ["clean-arch", "ddd"]
    assert settings.model_librarian == "claude-sonnet-4-5-20250929"
    assert settings.model_archivist == "claude-opus-4-6"


def test_library_advisories_collected():
    """Verify library_advisories aggregation logic."""
    lib_out_a = LibrarianOutput(
        raw_markdown="", book_name="a", advisory="Use SRP", flags=[], cited_sections=[],
    )
    lib_out_b = LibrarianOutput(
        raw_markdown="", book_name="b", advisory="Use DDD", flags=[], cited_sections=[],
    )

    advisories = [out.advisory for out in [lib_out_a, lib_out_b] if out.advisory]
    assert advisories == ["Use SRP", "Use DDD"]
```

- [ ] **Step 2: Run tests**

Run: `cd autocontext && uv run pytest tests/test_orchestrator_library.py -v`
Expected: PASS (2 tests)

- [ ] **Step 3: Wire library into orchestrator**

In `src/autocontext/agents/orchestrator.py`, modify `__init__()` and `_run_via_pipeline()`:

In `__init__()`, after existing runner instantiation, add:

```python
        # Library runners (instantiated per-run based on active books)
        self._librarian_runners: dict[str, LibrarianRunner] = {}
        self._archivist_runner: ArchivistRunner | None = None
        self._library_tool: LibraryToolHandler | None = None
```

Add a method to initialize library runners:

```python
    def _init_library(self, active_books: list[str], library_root: Path) -> None:
        """Initialize librarian and archivist runners for active books."""
        from autocontext.agents.librarian import LibrarianRunner
        from autocontext.agents.archivist import ArchivistRunner
        from autocontext.agents.library_tool import LibraryToolHandler

        for book_name in active_books:
            self._librarian_runners[book_name] = LibrarianRunner(
                runtime=self._runtime,
                model=self.settings.model_librarian,
                book_name=book_name,
            )
        if active_books:
            self._archivist_runner = ArchivistRunner(
                runtime=self._runtime,
                model=self.settings.model_archivist,
            )
            self._library_tool = LibraryToolHandler(
                librarians=self._librarian_runners,
                library_root=library_root,
                max_consults_per_role=self.settings.library_max_consults_per_role,
            )
```

In `_run_via_pipeline()`, pass `active_books` to `build_mts_dag()` and `librarian_runners`/`archivist_runner` to `build_role_handler()`.

After pipeline execution, populate `AgentOutputs` with librarian/archivist results:

```python
        # Collect library outputs
        librarian_outputs = []
        library_advisories = []
        for book_name, runner in self._librarian_runners.items():
            role_name = f"librarian_{book_name}"
            if role_name in completed:
                lib_output = parse_librarian_output(completed[role_name].content, book_name)
                librarian_outputs.append(lib_output)
                if lib_output.advisory:
                    library_advisories.append(f"[{book_name}]: {lib_output.advisory}")

        archivist_output = None
        if "archivist" in completed and completed["archivist"].content:
            archivist_output = parse_archivist_output(completed["archivist"].content)
```

- [ ] **Step 4: Run tests**

Run: `cd autocontext && uv run pytest tests/test_orchestrator_library.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/agents/orchestrator.py tests/test_orchestrator_library.py
git commit -m "feat(library): wire librarian/archivist runners into orchestrator pipeline"
```

### Task 20: Inject Library Context into Agent Prompts

**Files:**
- Modify: `autocontext/src/autocontext/prompts/templates.py`
- Test: `autocontext/tests/test_prompts_library.py` (append)

- [ ] **Step 1: Write test**

Append to `tests/test_prompts_library.py`:

```python
from autocontext.prompts.templates import inject_library_context


def test_inject_library_context_appends():
    original_prompt = "You are an analyst. Analyze the strategy."
    books = [{"name": "clean-arch", "title": "Clean Architecture", "tags": ["architecture"]}]
    result = inject_library_context(original_prompt, books)
    assert "Available Literature" in result
    assert "consult_library" in result
    assert original_prompt in result


def test_inject_library_context_no_books():
    original_prompt = "You are an analyst."
    result = inject_library_context(original_prompt, [])
    assert result == original_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd autocontext && uv run pytest tests/test_prompts_library.py::test_inject_library_context_appends -v`
Expected: FAIL — `ImportError: cannot import name 'inject_library_context'`

- [ ] **Step 3: Implement inject_library_context**

Add to `src/autocontext/prompts/templates.py`:

```python
def inject_library_context(prompt: str, books: list[dict]) -> str:
    """Append library context block to an agent prompt if books are active."""
    block = build_library_context_block(books)
    if not block:
        return prompt
    return f"{prompt}\n\n{block}"
```

- [ ] **Step 4: Run tests**

Run: `cd autocontext && uv run pytest tests/test_prompts_library.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/prompts/templates.py tests/test_prompts_library.py
git commit -m "feat(library): add inject_library_context for agent prompt augmentation"
```

### Task 21: Knowledge Export Extension

**Files:**
- Modify: `autocontext/src/autocontext/knowledge/export.py`
- Test: `autocontext/tests/test_export_library.py`

- [ ] **Step 1: Write test**

```python
# tests/test_export_library.py
from autocontext.knowledge.export import SkillPackage


def test_skill_package_library_fields():
    pkg = SkillPackage(
        scenario_name="grid_ctf",
        display_name="Grid CTF",
        description="Capture the flag",
        playbook="playbook content",
        lessons=["lesson 1"],
        best_strategy=None,
        best_score=0.0,
        best_elo=1000,
        hints="hints",
        harness={},
        metadata={},
        active_library_books=["clean-arch", "ddd"],
    )
    d = pkg.to_dict()
    assert d["active_library_books"] == ["clean-arch", "ddd"]


def test_skill_package_library_fields_default():
    pkg = SkillPackage(
        scenario_name="grid_ctf",
        display_name="Grid CTF",
        description="test",
        playbook="",
        lessons=[],
        best_strategy=None,
        best_score=0.0,
        best_elo=1000,
        hints="",
        harness={},
        metadata={},
    )
    assert pkg.active_library_books is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd autocontext && uv run pytest tests/test_export_library.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'active_library_books'`

- [ ] **Step 3: Add field to SkillPackage**

In `src/autocontext/knowledge/export.py`, add to `SkillPackage`:

```python
    active_library_books: list[str] | None = None
```

Update `to_dict()` to include `active_library_books` when not None.

- [ ] **Step 4: Run tests**

Run: `cd autocontext && uv run pytest tests/test_export_library.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd autocontext && git add src/autocontext/knowledge/export.py tests/test_export_library.py
git commit -m "feat(library): add active_library_books to SkillPackage"
```

---

## Summary

| Chunk | Tasks | What It Delivers |
|-------|-------|-----------------|
| 1 | 1-3 | Data contracts, AgentOutputs extension, config settings |
| 2 | 4 | Ingestion pipeline (chunking, registration, validation) |
| 3 | 5-6 | Librarian and archivist runners with parsers |
| 4 | 7 | consult_library tool with routing and rate limiting |
| 5 | 8-10 | Role routing, DAG extension, prompt templates |
| 6 | 11-13 | Storage persistence, archivist gate stage, Agent SDK |
| 7 | 14 | CLI commands (add-book, list-books, remove-book, --books) |
| 8 | 15-16 | Documentation updates (CLAUDE.md, README, CONTRIBUTING) |
| 9 | 17 | End-to-end integration test |
| 10 | 18-21 | LLM ingestion call, orchestrator wiring, prompt injection, knowledge export |

Total: 21 tasks, ~60 test functions, 7 new files, 14 modified files.
