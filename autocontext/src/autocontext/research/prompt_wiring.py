"""Research prompt wiring — format briefs for LLM injection (AC-501).

ResearchPromptInjector formats a ResearchBrief into a prompt section,
handling truncation to a char budget, confidence-based ordering, and
citation formatting. Supports placeholder injection or append-to-base.
"""

from __future__ import annotations

import logging

from autocontext.research.consultation import ResearchBrief

logger = logging.getLogger(__name__)

RESEARCH_PLACEHOLDER = "{research}"
DEFAULT_MAX_CHARS = 4000


class ResearchPromptInjector:
    """Formats research briefs and injects them into prompt templates."""

    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self._max_chars = max_chars

    def format_brief(self, brief: ResearchBrief) -> str:
        """Render a brief as a markdown section, truncated to budget.

        Findings are ordered by confidence (highest first).
        Returns empty string if brief has no findings.
        """
        if not brief.findings:
            return ""

        sorted_findings = sorted(brief.findings, key=lambda f: f.confidence, reverse=True)

        parts: list[str] = [f"## External Research: {brief.goal}\n"]
        budget = self._max_chars - len(parts[0])

        for f in sorted_findings:
            block_lines = [f"**{f.query_topic}** (confidence: {f.confidence:.0%})"]
            block_lines.append(f.summary)
            for c in f.citations:
                if c.url:
                    block_lines.append(f"- [{c.source}]({c.url})")
                else:
                    block_lines.append(f"- {c.source}")
            block_lines.append("")
            block = "\n".join(block_lines)

            if len(block) > budget:
                if len(parts) == 1:
                    # At least include one truncated finding
                    parts.append(block[:budget])
                break
            parts.append(block)
            budget -= len(block)

        return "\n".join(parts)

    def inject(self, base_prompt: str, brief: ResearchBrief) -> str:
        """Inject formatted brief into a prompt template.

        If base_prompt contains {research}, replaces it.
        Otherwise appends the section after the base.
        Returns base_prompt unchanged if brief is empty.
        """
        section = self.format_brief(brief)
        if not section:
            return base_prompt

        if RESEARCH_PLACEHOLDER in base_prompt:
            return base_prompt.replace(RESEARCH_PLACEHOLDER, section)

        return f"{base_prompt}\n\n{section}"
