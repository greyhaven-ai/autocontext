"""Tests for external research adapter contract (AC-497).

DDD: ResearchAdapter is a Protocol for pluggable research backends.
ResearchQuery/ResearchResult are the domain value objects.
"""

from __future__ import annotations

import pytest


class TestResearchQuery:
    """Query value object — what we're asking."""

    def test_create_query(self) -> None:
        from autocontext.research.types import ResearchQuery

        query = ResearchQuery(
            topic="OAuth2 best practices for Python APIs",
            context="Building a FastAPI service with JWT auth",
            max_results=5,
        )
        assert query.topic
        assert query.max_results == 5

    def test_urgency_levels(self) -> None:
        from autocontext.research.types import ResearchQuery, Urgency

        q = ResearchQuery(topic="test", urgency=Urgency.HIGH)
        assert q.urgency == Urgency.HIGH


class TestResearchResult:
    """Result value object — what comes back with citations."""

    def test_create_result(self) -> None:
        from autocontext.research.types import Citation, ResearchResult

        result = ResearchResult(
            query_topic="OAuth2",
            summary="Use PKCE flow for public clients",
            citations=[
                Citation(source="RFC 7636", url="https://tools.ietf.org/html/rfc7636", relevance=0.95),
                Citation(source="OWASP Guide", url="https://owasp.org/auth", relevance=0.85),
            ],
            confidence=0.9,
        )
        assert len(result.citations) == 2
        assert result.confidence == 0.9
        assert result.has_citations

    def test_empty_result(self) -> None:
        from autocontext.research.types import ResearchResult

        result = ResearchResult(query_topic="obscure topic", summary="No results found")
        assert not result.has_citations
        assert result.confidence == 0.0


class TestCitation:
    """Citation tracks provenance."""

    def test_citation_fields(self) -> None:
        from autocontext.research.types import Citation

        cite = Citation(source="RFC 7636", url="https://example.com", relevance=0.9)
        assert cite.source == "RFC 7636"
        assert cite.relevance == 0.9

    def test_citation_without_url(self) -> None:
        from autocontext.research.types import Citation

        cite = Citation(source="Internal docs")
        assert cite.url == ""
        assert cite.relevance == 0.0


class TestResearchAdapter:
    """Protocol — pluggable research backends."""

    def test_stub_adapter_satisfies_protocol(self) -> None:
        from autocontext.research.types import ResearchAdapter, ResearchQuery, ResearchResult

        class StubAdapter:
            def search(self, query: ResearchQuery) -> ResearchResult:
                return ResearchResult(
                    query_topic=query.topic,
                    summary=f"Stub result for: {query.topic}",
                    confidence=0.5,
                )

        adapter: ResearchAdapter = StubAdapter()
        result = adapter.search(ResearchQuery(topic="test"))
        assert result.summary.startswith("Stub result")


class TestResearchConfig:
    """Opt-in settings surface."""

    def test_default_disabled(self) -> None:
        from autocontext.research.types import ResearchConfig

        config = ResearchConfig()
        assert not config.enabled

    def test_enable_with_adapter(self) -> None:
        from autocontext.research.types import ResearchConfig

        config = ResearchConfig(enabled=True, adapter_name="perplexity")
        assert config.enabled
        assert config.adapter_name == "perplexity"

    def test_max_queries_per_session(self) -> None:
        from autocontext.research.types import ResearchConfig

        config = ResearchConfig(enabled=True, max_queries_per_session=10)
        assert config.max_queries_per_session == 10
