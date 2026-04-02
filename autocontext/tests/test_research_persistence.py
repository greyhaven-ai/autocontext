"""Tests for research evidence persistence (AC-500).

DDD: ResearchStore persists briefs and results for audit trail,
cross-session learning, and prompt context windows.
"""

from __future__ import annotations

from pathlib import Path

from autocontext.research.types import Citation, ResearchResult


def _make_brief(goal: str = "test", n_findings: int = 2):
    from autocontext.research.consultation import ResearchBrief

    results = [
        ResearchResult(
            query_topic=f"topic-{i}",
            summary=f"Summary {i}",
            confidence=0.5 + i * 0.1,
            citations=[Citation(source=f"src-{i}", url=f"https://example.com/{i}", relevance=0.8)],
        )
        for i in range(n_findings)
    ]
    return ResearchBrief.from_results(goal=goal, results=results)


class TestResearchStore:
    def test_save_and_load_brief(self, tmp_path: Path) -> None:
        from autocontext.research.persistence import ResearchStore

        store = ResearchStore(tmp_path)
        brief = _make_brief("Build auth API")

        ref = store.save_brief("session-1", brief)
        assert ref.session_id == "session-1"
        assert ref.brief_id

        loaded = store.load_brief(ref.brief_id)
        assert loaded is not None
        assert loaded.goal == "Build auth API"
        assert len(loaded.findings) == 2

    def test_list_briefs_by_session(self, tmp_path: Path) -> None:
        from autocontext.research.persistence import ResearchStore

        store = ResearchStore(tmp_path)
        store.save_brief("s1", _make_brief("goal-a"))
        store.save_brief("s1", _make_brief("goal-b"))
        store.save_brief("s2", _make_brief("goal-c"))

        s1_briefs = store.list_briefs("s1")
        assert len(s1_briefs) == 2
        assert store.list_briefs("s2") == [store.list_briefs("s2")[0]]

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        from autocontext.research.persistence import ResearchStore

        store = ResearchStore(tmp_path)
        assert store.load_brief("nonexistent") is None

    def test_briefs_persist_across_instances(self, tmp_path: Path) -> None:
        from autocontext.research.persistence import ResearchStore

        store1 = ResearchStore(tmp_path)
        ref = store1.save_brief("s1", _make_brief("persistent"))

        store2 = ResearchStore(tmp_path)
        loaded = store2.load_brief(ref.brief_id)
        assert loaded is not None
        assert loaded.goal == "persistent"

    def test_manifest_merges_across_store_instances(self, tmp_path: Path) -> None:
        from autocontext.research.persistence import ResearchStore

        store1 = ResearchStore(tmp_path)
        store2 = ResearchStore(tmp_path)

        ref1 = store1.save_brief("s1", _make_brief("goal-a"))
        ref2 = store2.save_brief("s2", _make_brief("goal-b"))

        store3 = ResearchStore(tmp_path)
        assert store3.brief_count() == 2
        assert len(store3.list_briefs("s1")) == 1
        assert len(store3.list_briefs("s2")) == 1
        assert store3.load_brief(ref1.brief_id) is not None
        assert store3.load_brief(ref2.brief_id) is not None

    def test_citations_round_trip(self, tmp_path: Path) -> None:
        from autocontext.research.persistence import ResearchStore

        store = ResearchStore(tmp_path)
        brief = _make_brief("cite-test", n_findings=1)
        ref = store.save_brief("s1", brief)

        loaded = store.load_brief(ref.brief_id)
        assert loaded is not None
        assert len(loaded.unique_citations) == 1
        assert loaded.unique_citations[0].source == "src-0"

    def test_brief_count(self, tmp_path: Path) -> None:
        from autocontext.research.persistence import ResearchStore

        store = ResearchStore(tmp_path)
        assert store.brief_count() == 0
        store.save_brief("s1", _make_brief())
        store.save_brief("s1", _make_brief())
        assert store.brief_count() == 2

    def test_delete_brief(self, tmp_path: Path) -> None:
        from autocontext.research.persistence import ResearchStore

        store = ResearchStore(tmp_path)
        ref = store.save_brief("s1", _make_brief())
        assert store.load_brief(ref.brief_id) is not None

        deleted = store.delete_brief(ref.brief_id)
        assert deleted is True
        assert store.load_brief(ref.brief_id) is None
        assert store.brief_count() == 0
