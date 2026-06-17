from pathlib import Path

from autocontext.knowledge.pending_lessons import PendingLessonStore
from autocontext.loop.stage_helpers.persistence_helpers import _sync_structured_lessons
from autocontext.storage.artifacts import ArtifactStore


class _Outputs:
    def __init__(self, lessons: list[str]) -> None:
        self.coach_lessons = lessons


class _Ctx:
    def __init__(self, artifacts: ArtifactStore, mode: str, lessons: list[str]) -> None:
        self.scenario_name = "scn"
        self.generation = 7
        self.previous_best = 0.5
        self.gate_decision = "advance"
        self.curator_approval_mode = mode
        self.outputs = _Outputs(lessons)


def _artifacts(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )


def test_auto_mode_feeds_active_only(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _sync_structured_lessons(_Ctx(art, "auto", ["- avoid X", "use Y"]), artifacts=art)
    assert sorted(les.text for les in art.lesson_store.read_lessons("scn")) == ["avoid X", "use Y"]
    assert PendingLessonStore(art.knowledge_root).read("scn") == []


def test_approve_mode_feeds_pending_only(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _sync_structured_lessons(_Ctx(art, "approve", ["hold me"]), artifacts=art)
    assert art.lesson_store.read_lessons("scn") == []
    assert [les.text for les in PendingLessonStore(art.knowledge_root).read("scn")] == ["hold me"]


def test_review_mode_feeds_both(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _sync_structured_lessons(_Ctx(art, "review", ["both"]), artifacts=art)
    assert [les.text for les in art.lesson_store.read_lessons("scn")] == ["both"]
    assert [les.text for les in PendingLessonStore(art.knowledge_root).read("scn")] == ["both"]


def test_sync_skips_when_not_advancing(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    ctx = _Ctx(art, "auto", ["x"])
    ctx.gate_decision = "retry"
    _sync_structured_lessons(ctx, artifacts=art)
    assert art.lesson_store.read_lessons("scn") == []


def test_sync_dedupes_existing(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _sync_structured_lessons(_Ctx(art, "auto", ["dup"]), artifacts=art)
    _sync_structured_lessons(_Ctx(art, "auto", ["dup", "new"]), artifacts=art)
    assert sorted(les.text for les in art.lesson_store.read_lessons("scn")) == ["dup", "new"]
