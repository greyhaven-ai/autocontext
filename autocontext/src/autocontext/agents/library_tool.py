"""consult_library tool: cross-agent literature consultation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from autocontext.agents.librarian import LibrarianRunner


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
