from pathlib import Path

from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson
from autocontext.knowledge.pending_lessons import PendingLessonStore


def _lesson(text: str, lid: str) -> Lesson:
    return Lesson(id=lid, text=text, meta=ApplicabilityMeta(created_at="", generation=1, best_score=0.5))


def test_add_read_roundtrip(tmp_path: Path) -> None:
    store = PendingLessonStore(tmp_path)
    store.add("scn", _lesson("avoid X", "lesson_a"))
    out = store.read("scn")
    assert [les.text for les in out] == ["avoid X"]


def test_add_dedupes_by_text_and_id(tmp_path: Path) -> None:
    store = PendingLessonStore(tmp_path)
    store.add("scn", _lesson("avoid X", "lesson_a"))
    store.add("scn", _lesson("avoid X", "lesson_b"))  # same text
    store.add("scn", _lesson("other", "lesson_a"))  # same id
    assert len(store.read("scn")) == 1


def test_remove_returns_and_persists(tmp_path: Path) -> None:
    store = PendingLessonStore(tmp_path)
    store.add("scn", _lesson("avoid X", "lesson_a"))
    removed = store.remove("scn", "lesson_a")
    assert removed is not None and removed.text == "avoid X"
    assert store.read("scn") == []
    assert store.remove("scn", "lesson_a") is None  # idempotent


def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    assert PendingLessonStore(tmp_path).read("never") == []
