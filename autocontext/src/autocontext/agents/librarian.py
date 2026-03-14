"""Librarian agent: literature-aware advisory and flagging."""

from __future__ import annotations

import re

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
