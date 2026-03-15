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
