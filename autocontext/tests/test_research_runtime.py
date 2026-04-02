"""Tests for research runtime plumbing (AC-498).

DDD: ResearchSession extends Session with research capabilities.
Research is gated by config and tracked at session level.
"""

from __future__ import annotations

from autocontext.research.types import ResearchQuery, ResearchResult


class StubAdapter:
    """Stub that satisfies ResearchAdapter protocol."""

    def __init__(self, response: str = "Stub result") -> None:
        self._response = response
        self.call_count = 0

    def search(self, query: ResearchQuery) -> ResearchResult:
        self.call_count += 1
        return ResearchResult(
            query_topic=query.topic,
            summary=self._response,
            confidence=0.8,
        )


class TestResearchEnabledSession:
    """Session with research adapter attached."""

    def test_session_accepts_research_adapter(self) -> None:
        from autocontext.research.runtime import ResearchEnabledSession

        adapter = StubAdapter()
        session = ResearchEnabledSession.create(
            goal="Build API", research_adapter=adapter
        )
        assert session.has_research
        assert session.research_queries_used == 0

    def test_session_without_adapter(self) -> None:
        from autocontext.research.runtime import ResearchEnabledSession

        session = ResearchEnabledSession.create(goal="Build API")
        assert not session.has_research

    def test_disabled_config_blocks_research(self) -> None:
        from autocontext.research.runtime import ResearchEnabledSession
        from autocontext.research.types import ResearchConfig, ResearchQuery

        adapter = StubAdapter()
        session = ResearchEnabledSession.create(
            goal="Build API",
            research_adapter=adapter,
            research_config=ResearchConfig(enabled=False, max_queries_per_session=5),
        )

        assert not session.has_research
        assert session.research(ResearchQuery(topic="auth best practices")) is None
        assert session.research_queries_used == 0
        assert adapter.call_count == 0

    def test_research_query_during_session(self) -> None:
        from autocontext.research.runtime import ResearchEnabledSession
        from autocontext.research.types import ResearchQuery

        adapter = StubAdapter("OAuth2 is best for APIs")
        session = ResearchEnabledSession.create(goal="test", research_adapter=adapter)

        result = session.research(ResearchQuery(topic="auth best practices"))
        assert result is not None
        assert "OAuth2" in result.summary
        assert session.research_queries_used == 1
        assert adapter.call_count == 1

    def test_research_without_adapter_returns_none(self) -> None:
        from autocontext.research.runtime import ResearchEnabledSession
        from autocontext.research.types import ResearchQuery

        session = ResearchEnabledSession.create(goal="test")
        result = session.research(ResearchQuery(topic="anything"))
        assert result is None

    def test_research_respects_budget(self) -> None:
        from autocontext.research.runtime import ResearchEnabledSession
        from autocontext.research.types import ResearchConfig, ResearchQuery

        adapter = StubAdapter()
        config = ResearchConfig(enabled=True, max_queries_per_session=2)
        session = ResearchEnabledSession.create(
            goal="test", research_adapter=adapter, research_config=config
        )

        session.research(ResearchQuery(topic="q1"))
        session.research(ResearchQuery(topic="q2"))
        result = session.research(ResearchQuery(topic="q3"))
        assert result is None  # budget exhausted
        assert session.research_queries_used == 2

    def test_research_events_emitted(self) -> None:
        from autocontext.research.runtime import ResearchEnabledSession
        from autocontext.research.types import ResearchQuery

        adapter = StubAdapter()
        session = ResearchEnabledSession.create(goal="test", research_adapter=adapter)
        session.research(ResearchQuery(topic="auth"))

        event_types = [e.eventType for e in session.events]
        assert "research_requested" in event_types

    def test_research_results_accumulated(self) -> None:
        from autocontext.research.runtime import ResearchEnabledSession
        from autocontext.research.types import ResearchQuery

        adapter = StubAdapter()
        session = ResearchEnabledSession.create(goal="test", research_adapter=adapter)
        session.research(ResearchQuery(topic="q1"))
        session.research(ResearchQuery(topic="q2"))

        assert len(session.research_history) == 2
        assert session.research_history[0].query_topic == "q1"
