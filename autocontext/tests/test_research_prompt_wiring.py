"""Tests for research prompt wiring (AC-501).

DDD: ResearchPromptInjector formats briefs into prompt sections
for LLM context injection. Handles budget, truncation, ordering.
"""

from __future__ import annotations

from autocontext.research.consultation import ResearchBrief
from autocontext.research.types import Citation, ResearchResult


def _brief(n: int = 2, confidence: float = 0.8) -> ResearchBrief:
    results = [
        ResearchResult(
            query_topic=f"topic-{i}",
            summary=f"Finding about topic-{i} with detailed explanation",
            confidence=confidence,
            citations=[Citation(source=f"source-{i}", url=f"https://example.com/{i}", relevance=0.9)],
        )
        for i in range(n)
    ]
    return ResearchBrief.from_results(goal="Build API", results=results)


class TestResearchPromptInjector:
    def test_inject_brief_as_section(self) -> None:
        from autocontext.research.prompt_wiring import ResearchPromptInjector

        injector = ResearchPromptInjector()
        section = injector.format_brief(_brief())

        assert "## External Research" in section
        assert "topic-0" in section
        assert "topic-1" in section

    def test_empty_brief_returns_empty(self) -> None:
        from autocontext.research.prompt_wiring import ResearchPromptInjector

        injector = ResearchPromptInjector()
        section = injector.format_brief(ResearchBrief.empty("test"))
        assert section == ""

    def test_respects_token_budget(self) -> None:
        from autocontext.research.prompt_wiring import ResearchPromptInjector

        brief = _brief(n=20)
        injector = ResearchPromptInjector(max_chars=500)
        section = injector.format_brief(brief)
        assert len(section) <= 550  # small overflow tolerance for final line

    def test_highest_confidence_first(self) -> None:
        from autocontext.research.prompt_wiring import ResearchPromptInjector

        results = [
            ResearchResult(query_topic="low", summary="Low conf", confidence=0.3),
            ResearchResult(query_topic="high", summary="High conf", confidence=0.9),
            ResearchResult(query_topic="mid", summary="Mid conf", confidence=0.6),
        ]
        brief = ResearchBrief.from_results(goal="test", results=results)
        injector = ResearchPromptInjector()
        section = injector.format_brief(brief)

        high_pos = section.index("high")
        low_pos = section.index("low")
        assert high_pos < low_pos

    def test_inject_into_prompt(self) -> None:
        from autocontext.research.prompt_wiring import ResearchPromptInjector

        injector = ResearchPromptInjector()
        base = "You are a helpful assistant.\n\n{research}\n\nPlease help the user."
        result = injector.inject(base, _brief())
        assert "External Research" in result
        assert "Please help the user" in result

    def test_inject_no_placeholder_appends(self) -> None:
        from autocontext.research.prompt_wiring import ResearchPromptInjector

        injector = ResearchPromptInjector()
        base = "You are a helpful assistant."
        result = injector.inject(base, _brief())
        assert result.startswith("You are a helpful assistant.")
        assert "External Research" in result

    def test_inject_empty_brief_returns_base(self) -> None:
        from autocontext.research.prompt_wiring import ResearchPromptInjector

        injector = ResearchPromptInjector()
        base = "You are a helpful assistant."
        result = injector.inject(base, ResearchBrief.empty("test"))
        assert result == base

    def test_citation_formatting(self) -> None:
        from autocontext.research.prompt_wiring import ResearchPromptInjector

        injector = ResearchPromptInjector()
        section = injector.format_brief(_brief(n=1))
        assert "source-0" in section
        assert "https://example.com/0" in section
