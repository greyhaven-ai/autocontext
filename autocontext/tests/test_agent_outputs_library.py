"""Tests for librarian/archivist fields on AgentOutputs."""
from __future__ import annotations

from autocontext.agents.contracts import ArchivistOutput, LibrarianOutput
from autocontext.agents.types import AgentOutputs


def test_agent_outputs_library_defaults() -> None:
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


def test_agent_outputs_with_library_data() -> None:
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
