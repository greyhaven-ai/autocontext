"""End-to-end test: book ingestion -> DAG with librarians -> archivist gate."""
from __future__ import annotations

import json
from pathlib import Path

from autocontext.agents.archivist import has_violations
from autocontext.agents.contracts import ArchivistDecision, ArchivistOutput, LibrarianFlag, LibrarianOutput
from autocontext.agents.librarian import parse_librarian_output
from autocontext.agents.pipeline_adapter import build_mts_dag
from autocontext.knowledge.ingestion import register_book
from autocontext.loop.stage_archivist_gate import evaluate_archivist_gate
from autocontext.storage.artifacts import ArtifactStore


def test_full_library_flow(tmp_path: Path) -> None:
    """Test the complete flow: ingest book -> build DAG -> run librarians -> gate."""
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
    assert meta["chapter_count"] >= 1
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


def test_no_violations_skips_archivist() -> None:
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
