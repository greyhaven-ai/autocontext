"""Research consultation — goal decomposition and brief assembly (AC-499).

Domain service: ResearchConsultant decomposes a goal into targeted queries,
executes them through a ResearchEnabledSession, filters weak signals, deduplicates
citations, and packages everything into a ResearchBrief value object.

ResearchBrief is a frozen value object suitable for downstream prompt injection.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import BaseModel, Field

from autocontext.research.runtime import ResearchEnabledSession
from autocontext.research.types import Citation, ResearchQuery, ResearchResult, Urgency

logger = logging.getLogger(__name__)


class ResearchBrief(BaseModel):
    """Immutable snapshot of research findings for a goal.

    Produced by ResearchConsultant, consumed by prompt wiring (AC-501).
    """

    goal: str
    findings: list[ResearchResult] = Field(default_factory=list)
    unique_citations: list[Citation] = Field(default_factory=list)

    model_config = {"frozen": True}

    @property
    def avg_confidence(self) -> float:
        if not self.findings:
            return 0.0
        return sum(f.confidence for f in self.findings) / len(self.findings)

    @classmethod
    def from_results(
        cls,
        goal: str,
        results: Sequence[ResearchResult],
        min_confidence: float = 0.0,
    ) -> ResearchBrief:
        filtered = [r for r in results if r.confidence >= min_confidence]
        citations = _dedupe_citations(filtered)
        return cls(goal=goal, findings=list(filtered), unique_citations=citations)

    @classmethod
    def empty(cls, goal: str) -> ResearchBrief:
        return cls(goal=goal)

    def to_markdown(self) -> str:
        if not self.findings:
            return f"## Research Brief: {self.goal}\n\nNo findings available.\n"

        parts = [f"## Research Brief: {self.goal}\n"]
        for f in self.findings:
            parts.append(f"### {f.query_topic} (confidence: {f.confidence:.0%})\n")
            parts.append(f"{f.summary}\n")
            for c in f.citations:
                label = f"[{c.source}]({c.url})" if c.url else c.source
                parts.append(f"- {label}")
                if c.snippet:
                    parts.append(f"  > {c.snippet}")
            parts.append("")
        return "\n".join(parts)


def _dedupe_citations(results: Sequence[ResearchResult]) -> list[Citation]:
    """Collect unique citations across results, keyed by (source, url)."""
    seen: set[tuple[str, str]] = set()
    unique: list[Citation] = []
    for r in results:
        for c in r.citations:
            key = (c.source, c.url)
            if key not in seen:
                seen.add(key)
                unique.append(c)
    return unique


class ResearchConsultant:
    """Domain service: decompose goal → queries → brief.

    Stateless — create one and call .consult() per research need.
    """

    def __init__(
        self,
        urgency: Urgency = Urgency.NORMAL,
        min_confidence: float = 0.0,
    ) -> None:
        self._urgency = urgency
        self._min_confidence = min_confidence

    def consult(
        self,
        session: ResearchEnabledSession,
        topics: Sequence[str],
        context: str = "",
    ) -> ResearchBrief:
        """Execute research queries and return a packaged brief.

        Respects the session's budget — stops when budget is exhausted.
        Filters results below min_confidence.
        """
        if not session.has_research:
            logger.debug("No research adapter attached — returning empty brief")
            return ResearchBrief.empty(session.goal)

        results: list[ResearchResult] = []
        for topic in topics:
            query = ResearchQuery(
                topic=topic,
                context=context,
                urgency=self._urgency,
            )
            result = session.research(query)
            if result is None:
                logger.debug("Budget exhausted after %d queries", len(results))
                break
            results.append(result)

        return ResearchBrief.from_results(
            goal=session.goal,
            results=results,
            min_confidence=self._min_confidence,
        )
