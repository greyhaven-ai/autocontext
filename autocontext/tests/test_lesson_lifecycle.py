from pathlib import Path

from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore
from autocontext.knowledge.lifecycle import (
    approve_lesson,
    build_lifecycle,
    curate_lesson,
    reject_lesson,
)
from autocontext.storage.artifacts import ArtifactStore


def _meta(gen: int, approval_status: str = "active") -> ApplicabilityMeta:
    return ApplicabilityMeta(created_at="", generation=gen, best_score=0.5, approval_status=approval_status)


def _stores(tmp_path: Path) -> tuple[ArtifactStore, LessonStore]:
    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    return artifacts, artifacts.lesson_store


def test_build_lifecycle_buckets(tmp_path: Path) -> None:
    artifacts, store = _stores(tmp_path)
    store.write_lessons(
        "scn",
        [
            Lesson(id="a", text="fresh", meta=_meta(20)),
            Lesson(id="b", text="old", meta=_meta(2)),
            Lesson(id="p", text="held", meta=_meta(20, approval_status="pending")),
        ],
    )
    artifacts.append_dead_end("scn", "tried Y, lost")
    view = build_lifecycle(artifacts=artifacts, lesson_store=store, scenario="scn", current_generation=20)
    assert [v["text"] for v in view["active"]] == ["fresh"]
    assert [v["text"] for v in view["stale"]] == ["old"]
    assert [v["text"] for v in view["pending"]] == ["held"]
    assert view["deadEnd"] and "tried Y" in view["deadEnd"][0]["text"]
    assert view["deadEnd"][0]["id"].startswith("deadend_")


def test_approve_flips_pending_to_active(tmp_path: Path) -> None:
    _artifacts, store = _stores(tmp_path)
    store.write_lessons("scn", [Lesson(id="p", text="held", meta=_meta(5, approval_status="pending"))])
    assert approve_lesson(lesson_store=store, scenario="scn", lesson_id="p", current_generation=9) == "active"
    lesson = store.read_lessons("scn")[0]
    assert lesson.meta.approval_status == "active"
    assert lesson.meta.last_validated_gen == 9
    # idempotent: now active, not pending -> None
    assert approve_lesson(lesson_store=store, scenario="scn", lesson_id="p", current_generation=9) is None


def test_approve_non_pending_is_none(tmp_path: Path) -> None:
    _artifacts, store = _stores(tmp_path)
    store.write_lessons("scn", [Lesson(id="a", text="already active", meta=_meta(5))])
    assert approve_lesson(lesson_store=store, scenario="scn", lesson_id="a", current_generation=9) is None


def test_reject_removes(tmp_path: Path) -> None:
    _artifacts, store = _stores(tmp_path)
    store.write_lessons("scn", [Lesson(id="x", text="held", meta=_meta(5, approval_status="pending"))])
    assert reject_lesson(lesson_store=store, scenario="scn", lesson_id="x") is True
    assert store.read_lessons("scn") == []
    assert reject_lesson(lesson_store=store, scenario="scn", lesson_id="x") is False


def test_curate_actions(tmp_path: Path) -> None:
    artifacts, store = _stores(tmp_path)
    store.write_lessons("scn", [Lesson(id="x", text="lesson", meta=_meta(5))])
    assert (
        curate_lesson(
            artifacts=artifacts, lesson_store=store, scenario="scn", lesson_id="x", action="stale", current_generation=9
        )
        == "stale"
    )
    assert store.read_lessons("scn")[0].meta.last_validated_gen == -1

    store.write_lessons("scn", [Lesson(id="y", text="dead lesson", meta=_meta(5))])
    assert (
        curate_lesson(
            artifacts=artifacts, lesson_store=store, scenario="scn", lesson_id="y", action="deadEnd", current_generation=9
        )
        == "deadEnd"
    )
    assert store.read_lessons("scn") == []
    assert "dead lesson" in artifacts.read_dead_ends("scn")

    store.write_lessons("scn", [Lesson(id="z", text="gone", meta=_meta(5))])
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
