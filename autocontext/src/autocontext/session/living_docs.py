"""Opt-in living docs maintenance (AC-511).

Domain concepts:
- LivingDoc: entity tracking one opted-in document
- DocMaintainer: discovers opted-in docs, runs maintenance at safe boundaries
- DocUpdateResult: structured audit of what was checked and updated
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_OPT_IN_MARKER = "<!-- living-doc: true -->"


class LivingDoc:
    """Entity tracking one opted-in document.

    Docs opt in via a marker comment: <!-- living-doc: true -->
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.is_opted_in = True
        self.consultation_count = 0
        self._content: str | None = None

    @classmethod
    def from_path(cls, path: Path) -> LivingDoc | None:
        """Parse a file. Returns None if not opted in."""
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        if _OPT_IN_MARKER not in content:
            return None
        doc = cls(path)
        doc._content = content
        return doc

    def record_consultation(self) -> None:
        self.consultation_count += 1

    def read_content(self) -> str:
        if self._content is None:
            self._content = self.path.read_text(encoding="utf-8")
        return self._content


class DocUpdate(BaseModel):
    """One update applied to a living doc."""

    doc_path: str
    summary: str
    lines_changed: int = 0

    model_config = {"frozen": True}


class DocUpdateResult(BaseModel):
    """Structured audit of a maintenance pass."""

    docs_checked: int = 0
    updates: list[DocUpdate] = Field(default_factory=list)
    skipped: bool = False
    reason: str = ""

    model_config = {"frozen": True}


class DocMaintainer:
    """Discovers opted-in docs and runs maintenance.

    Maintenance only runs when:
    1. Feature is enabled
    2. There are learnings to promote
    3. Opted-in docs exist
    """

    def __init__(
        self,
        roots: list[Path] | None = None,
        enabled: bool = True,
    ) -> None:
        self._roots = roots or []
        self._enabled = enabled

    def discover(self) -> list[LivingDoc]:
        """Scan roots for opted-in markdown files."""
        docs: list[LivingDoc] = []
        for root in self._roots:
            if not root.is_dir():
                continue
            for md in sorted(root.rglob("*.md")):
                doc = LivingDoc.from_path(md)
                if doc is not None:
                    docs.append(doc)
        return docs

    def run(self, learnings: list[str]) -> DocUpdateResult:
        """Execute a maintenance pass.

        Returns structured result describing what was checked/updated.
        """
        if not self._enabled:
            return DocUpdateResult(skipped=True, reason="disabled")

        if not learnings:
            return DocUpdateResult(skipped=True, reason="No learnings to promote")

        docs = self.discover()
        if not docs:
            return DocUpdateResult(skipped=True, reason="No opted-in docs found")

        updates: list[DocUpdate] = []
        for doc in docs:
            # In the first version, we identify docs that could benefit
            # from updates based on learnings. Actual rewriting would
            # use an LLM call — for now we produce the audit trail.
            _content = doc.read_content()  # noqa: F841 — will be used by LLM rewriter
            relevant = [item for item in learnings if len(item.strip()) > 10]
            if relevant:
                updates.append(DocUpdate(
                    doc_path=str(doc.path),
                    summary=f"Candidate for update with {len(relevant)} learning(s)",
                ))

        logger.info("living docs: checked %d docs, %d candidates", len(docs), len(updates))
        return DocUpdateResult(
            docs_checked=len(docs),
            updates=updates,
        )
