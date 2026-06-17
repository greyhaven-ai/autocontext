from pathlib import Path

from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore
from autocontext.knowledge.lifecycle import (
    approve_lesson,
    build_lifecycle,
    curate_lesson,
    reject_lesson,
)
from autocontext.knowledge.pending_lessons import PendingLessonStore
from autocontext.storage.artifacts import ArtifactStore


def _meta(gen: int, last_validated: int | None = None) -> ApplicabilityMeta:
    m = ApplicabilityMeta(created_at="", generation=gen, best_score=0.5)
    if last_validated is not None:
        m.last_validated_gen = last_validated
    return m


def _stores(tmp_path: Path) -> tuple[ArtifactStore, LessonStore, PendingLessonStore]:
    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    return artifacts, artifacts.lesson_store, PendingLessonStore(tmp_path / "knowledge")


def test_build_lifecycle_buckets(tmp_path: Path) -> None:
    artifacts, store, pending = _stores(tmp_path)
    store.write_lessons(
        "scn",
        [
            Lesson(id="a", text="fresh", meta=_meta(20, 20)),
            Lesson(id="b", text="old", meta=_meta(2, 2)),
        ],
    )
    pending.add("scn", Lesson(id="p", text="held", meta=_meta(20, 20)))
    artifacts.append_dead_end("scn", "tried Y, lost")

    view = build_lifecycle(
        artifacts=artifacts,
        lesson_store=store,
        pending_store=pending,
        scenario="scn",
        current_generation=20,
    )
    assert [entry["text"] for entry in view["active"]] == ["fresh"]
    assert [entry["text"] for entry in view["stale"]] == ["old"]
    assert [entry["text"] for entry in view["pending"]] == ["held"]
    assert view["deadEnd"] and "tried Y" in view["deadEnd"][0]["text"]
    assert view["deadEnd"][0]["id"].startswith("deadend_")


def test_approve_moves_pending_to_active(tmp_path: Path) -> None:
    _artifacts, store, pending = _stores(tmp_path)
    pending.add("scn", Lesson(id="p", text="held", meta=_meta(5, 5)))
    status = approve_lesson(lesson_store=store, pending_store=pending, scenario="scn", lesson_id="p", current_generation=9)
    assert status == "active"
    assert [lesson.text for lesson in store.read_lessons("scn")] == ["held"]
    assert pending.read("scn") == []
    assert approve_lesson(lesson_store=store, pending_store=pending, scenario="scn", lesson_id="p", current_generation=9) is None


def test_approve_in_review_is_ack_only(tmp_path: Path) -> None:
    _artifacts, store, pending = _stores(tmp_path)
    store.write_lessons("scn", [Lesson(id="x", text="held", meta=_meta(5, 5))])
    pending.add("scn", Lesson(id="x", text="held", meta=_meta(5, 5)))
    status = approve_lesson(lesson_store=store, pending_store=pending, scenario="scn", lesson_id="x", current_generation=9)
    assert status == "active"
    assert len(store.read_lessons("scn")) == 1
    assert pending.read("scn") == []


def test_reject_removes_from_pending_and_active(tmp_path: Path) -> None:
    _artifacts, store, pending = _stores(tmp_path)
    store.write_lessons("scn", [Lesson(id="x", text="held", meta=_meta(5, 5))])
    pending.add("scn", Lesson(id="x", text="held", meta=_meta(5, 5)))
    assert reject_lesson(lesson_store=store, pending_store=pending, scenario="scn", lesson_id="x") is True
    assert store.read_lessons("scn") == []
    assert pending.read("scn") == []


def test_curate_actions(tmp_path: Path) -> None:
    artifacts, store, _pending = _stores(tmp_path)
    store.write_lessons("scn", [Lesson(id="x", text="lesson", meta=_meta(5, 5))])

    assert (
        curate_lesson(
            artifacts=artifacts, lesson_store=store, scenario="scn", lesson_id="x", action="stale", current_generation=9
        )
        == "stale"
    )
    assert store.read_lessons("scn")[0].meta.last_validated_gen == -1

    store.write_lessons("scn", [Lesson(id="y", text="dead lesson", meta=_meta(5, 5))])
    assert (
        curate_lesson(
            artifacts=artifacts, lesson_store=store, scenario="scn", lesson_id="y", action="deadEnd", current_generation=9
        )
        == "deadEnd"
    )
    assert store.read_lessons("scn") == []
    assert "dead lesson" in artifacts.read_dead_ends("scn")

    store.write_lessons("scn", [Lesson(id="z", text="gone", meta=_meta(5, 5))])
    assert (
        curate_lesson(
            artifacts=artifacts, lesson_store=store, scenario="scn", lesson_id="z", action="delete", current_generation=9
        )
        == "deleted"
    )
    assert store.read_lessons("scn") == []

    assert (
        curate_lesson(
            artifacts=artifacts, lesson_store=store, scenario="scn", lesson_id="missing", action="delete", current_generation=9
        )
        is None
    )
