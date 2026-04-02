"""Tests for research consultation service (AC-499).

DDD: ResearchConsultant is a domain service that:
- Decomposes a session goal into targeted research queries
- Executes them against the adapter via ResearchEnabledSession
- Filters low-confidence results
- Deduplicates citations
- Packages everything into a ResearchBrief value object
"""

from __future__ import annotations

import pytest

from autocontext.research.types import (
    Citation,
    ResearchConfig,
    ResearchQuery,
    ResearchResult,
)


class StubAdapter:
    """Returns predictable results keyed by topic."""

    def __init__(self, results: dict[str, ResearchResult] | None = None) -> None:
        self._results = results or {}
        self.queries_received: list[str] = []

    def search(self, query: ResearchQuery) -> ResearchResult:
        self.queries_received.append(query.topic)
        if query.topic in self._results:
            return self._results[query.topic]
        return ResearchResult(
            query_topic=query.topic,
            summary=f"Default answer for {query.topic}",
            confidence=0.5,
        )


def _make_result(topic: str, confidence: float = 0.8, citations: list[Citation] | None = None) -> ResearchResult:
    return ResearchResult(
        query_topic=topic,
        summary=f"Research on {topic}",
        confidence=confidence,
        citations=citations or [],
    )


# --- ResearchBrief value object ---

class TestResearchBrief:
    def test_brief_from_results(self) -> None:
        from autocontext.research.consultation import ResearchBrief

        brief = ResearchBrief.from_results(
            goal="Build auth API",
            results=[
                _make_result("OAuth2", confidence=0.9),
                _make_result("JWT tokens", confidence=0.7),
            ],
        )
        assert brief.goal == "Build auth API"
        assert len(brief.findings) == 2
        assert brief.avg_confidence == pytest.approx(0.8, abs=0.01)

    def test_brief_filters_low_confidence(self) -> None:
        from autocontext.research.consultation import ResearchBrief

        brief = ResearchBrief.from_results(
            goal="test",
            results=[
                _make_result("good", confidence=0.8),
                _make_result("weak", confidence=0.1),
            ],
            min_confidence=0.3,
        )
        assert len(brief.findings) == 1
        assert brief.findings[0].query_topic == "good"

    def test_brief_deduplicates_citations(self) -> None:
        from autocontext.research.consultation import ResearchBrief

        shared_cite = Citation(source="RFC 6749", url="https://tools.ietf.org/rfc6749", relevance=0.9)
        brief = ResearchBrief.from_results(
            goal="test",
            results=[
                _make_result("q1", citations=[shared_cite, Citation(source="Unique A", relevance=0.7)]),
                _make_result("q2", citations=[shared_cite, Citation(source="Unique B", relevance=0.6)]),
            ],
        )
        urls = [c.url for c in brief.unique_citations]
        assert urls.count("https://tools.ietf.org/rfc6749") == 1
        assert len(brief.unique_citations) == 3

    def test_brief_renders_markdown(self) -> None:
        from autocontext.research.consultation import ResearchBrief

        brief = ResearchBrief.from_results(
            goal="Build auth",
            results=[_make_result("OAuth2", confidence=0.9, citations=[
                Citation(source="RFC 6749", url="https://example.com/rfc", relevance=0.9),
            ])],
        )
        md = brief.to_markdown()
        assert "OAuth2" in md
        assert "RFC 6749" in md
        assert "Build auth" in md

    def test_empty_brief(self) -> None:
        from autocontext.research.consultation import ResearchBrief

        brief = ResearchBrief.empty("no results")
        assert len(brief.findings) == 0
        assert brief.avg_confidence == 0.0


# --- ResearchConsultant domain service ---

class TestResearchConsultant:
    def test_consult_decomposes_goal(self) -> None:
        from autocontext.research.consultation import ResearchConsultant
        from autocontext.research.runtime import ResearchEnabledSession

        adapter = StubAdapter()
        session = ResearchEnabledSession.create(goal="Build OAuth2 login", research_adapter=adapter)
        consultant = ResearchConsultant()

        brief = consultant.consult(session, topics=["OAuth2 best practices", "token storage"])
        assert len(brief.findings) == 2
        assert len(adapter.queries_received) == 2

    def test_consult_respects_session_budget(self) -> None:
        from autocontext.research.consultation import ResearchConsultant
        from autocontext.research.runtime import ResearchEnabledSession

        adapter = StubAdapter()
        config = ResearchConfig(enabled=True, max_queries_per_session=1)
        session = ResearchEnabledSession.create(goal="test", research_adapter=adapter, research_config=config)
        consultant = ResearchConsultant()

        brief = consultant.consult(session, topics=["t1", "t2", "t3"])
        # Only 1 query allowed by budget
        assert len(brief.findings) == 1

    def test_consult_without_adapter_returns_empty(self) -> None:
        from autocontext.research.consultation import ResearchConsultant
        from autocontext.research.runtime import ResearchEnabledSession

        session = ResearchEnabledSession.create(goal="test")
        consultant = ResearchConsultant()

        brief = consultant.consult(session, topics=["anything"])
        assert len(brief.findings) == 0

    def test_consult_applies_context(self) -> None:
        from autocontext.research.consultation import ResearchConsultant
        from autocontext.research.runtime import ResearchEnabledSession

        adapter = StubAdapter()
        session = ResearchEnabledSession.create(goal="Build API", research_adapter=adapter)
        consultant = ResearchConsultant()

        brief = consultant.consult(
            session,
            topics=["auth"],
            context="We use FastAPI with Python 3.12",
        )
        assert len(brief.findings) == 1

    def test_consult_filters_by_min_confidence(self) -> None:
        from autocontext.research.consultation import ResearchConsultant
        from autocontext.research.runtime import ResearchEnabledSession

        adapter = StubAdapter(results={
            "good": _make_result("good", confidence=0.9),
            "weak": _make_result("weak", confidence=0.1),
        })
        session = ResearchEnabledSession.create(goal="test", research_adapter=adapter)
        consultant = ResearchConsultant(min_confidence=0.3)

        brief = consultant.consult(session, topics=["good", "weak"])
        assert len(brief.findings) == 1
        assert brief.findings[0].query_topic == "good"
