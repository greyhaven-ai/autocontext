"""Tests for AC-236: Schema- and state-aware playbook invalidation and lesson applicability.

Verifies:
1. Lesson and ApplicabilityMeta dataclass construction and serialization.
2. LessonStore read/write/add/filter operations.
3. Staleness detection, supersession, and schema-change invalidation.
4. Backward-compatible migration from raw bullet strings.
5. Staleness reporting for operator visibility.
6. ArtifactStore integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. ApplicabilityMeta
# ---------------------------------------------------------------------------


class TestApplicabilityMeta:
    def test_construction_defaults(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=3,
            best_score=0.72,
        )
        assert meta.created_at == "2026-03-13T10:00:00Z"
        assert meta.generation == 3
        assert meta.best_score == 0.72
        assert meta.schema_version == ""
        assert meta.upstream_sig == ""
        assert meta.operation_type == "advance"
        assert meta.superseded_by == ""
        assert meta.last_validated_gen == 3  # defaults to creation generation

    def test_construction_full(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=5,
            best_score=0.85,
            schema_version="abc123",
            upstream_sig="dep_sig_456",
            operation_type="rollback",
            superseded_by="lesson_007",
            last_validated_gen=8,
        )
        assert meta.schema_version == "abc123"
        assert meta.upstream_sig == "dep_sig_456"
        assert meta.operation_type == "rollback"
        assert meta.superseded_by == "lesson_007"
        assert meta.last_validated_gen == 8

    def test_to_dict_roundtrip(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=3,
            best_score=0.72,
            schema_version="v1",
            upstream_sig="sig",
            operation_type="advance",
        )
        d = meta.to_dict()
        assert isinstance(d, dict)
        restored = ApplicabilityMeta.from_dict(d)
        assert restored == meta


# ---------------------------------------------------------------------------
# 2. Lesson
# ---------------------------------------------------------------------------


class TestLesson:
    def test_construction(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=3,
            best_score=0.72,
        )
        lesson = Lesson(id="lesson_001", text="- Aggressive strategies outperform passive ones", meta=meta)
        assert lesson.id == "lesson_001"
        assert lesson.text == "- Aggressive strategies outperform passive ones"
        assert lesson.meta is meta

    def test_to_dict_from_dict_roundtrip(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=3,
            best_score=0.72,
            schema_version="v1",
        )
        lesson = Lesson(id="lesson_001", text="- Some lesson", meta=meta)
        d = lesson.to_dict()
        assert isinstance(d, dict)
        restored = Lesson.from_dict(d)
        assert restored.id == lesson.id
        assert restored.text == lesson.text
        assert restored.meta == lesson.meta

    def test_is_stale_within_window(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=5,
            best_score=0.72,
            last_validated_gen=8,
        )
        lesson = Lesson(id="L1", text="- test", meta=meta)
        assert not lesson.is_stale(current_generation=12, staleness_window=10)

    def test_is_stale_outside_window(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=1,
            best_score=0.5,
            last_validated_gen=2,
        )
        lesson = Lesson(id="L1", text="- test", meta=meta)
        assert lesson.is_stale(current_generation=20, staleness_window=10)

    def test_is_superseded(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=1,
            best_score=0.5,
            superseded_by="lesson_002",
        )
        lesson = Lesson(id="L1", text="- old approach", meta=meta)
        assert lesson.is_superseded()

    def test_is_not_superseded(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=1,
            best_score=0.5,
        )
        lesson = Lesson(id="L1", text="- current approach", meta=meta)
        assert not lesson.is_superseded()

    def test_is_applicable(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=5,
            best_score=0.8,
            last_validated_gen=12,
        )
        lesson = Lesson(id="L1", text="- good", meta=meta)
        assert lesson.is_applicable(current_generation=15, staleness_window=10)

    def test_not_applicable_when_stale(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=1,
            best_score=0.5,
            last_validated_gen=2,
        )
        lesson = Lesson(id="L1", text="- old", meta=meta)
        assert not lesson.is_applicable(current_generation=20, staleness_window=10)

    def test_not_applicable_when_superseded(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=5,
            best_score=0.8,
            last_validated_gen=12,
            superseded_by="lesson_999",
        )
        lesson = Lesson(id="L1", text="- obsolete", meta=meta)
        assert not lesson.is_applicable(current_generation=15, staleness_window=10)

    def test_not_applicable_when_schema_invalidated(self) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=5,
            best_score=0.8,
            last_validated_gen=-1,
        )
        lesson = Lesson(id="L1", text="- invalidated", meta=meta)
        assert not lesson.is_applicable(current_generation=5, staleness_window=10)


# ---------------------------------------------------------------------------
# 3. LessonStore — read/write/add
# ---------------------------------------------------------------------------


class TestLessonStoreReadWrite:
    @pytest.fixture()
    def store(self, tmp_path: Path):
        from autocontext.knowledge.lessons import LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        return LessonStore(knowledge_root=knowledge, skills_root=skills)

    def test_read_empty(self, store) -> None:
        lessons = store.read_lessons("grid_ctf")
        assert lessons == []

    def test_write_and_read_roundtrip(self, store) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=1,
            best_score=0.5,
        )
        lessons = [Lesson(id="L1", text="- lesson one", meta=meta)]
        store.write_lessons("grid_ctf", lessons)
        restored = store.read_lessons("grid_ctf")
        assert len(restored) == 1
        assert restored[0].id == "L1"
        assert restored[0].text == "- lesson one"
        assert restored[0].meta == meta

    def test_add_lesson(self, store) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=3,
            best_score=0.72,
        )
        lesson = store.add_lesson("grid_ctf", "- new insight", meta)
        assert lesson.id  # non-empty ID assigned
        assert lesson.text == "- new insight"

        all_lessons = store.read_lessons("grid_ctf")
        assert len(all_lessons) == 1
        assert all_lessons[0].id == lesson.id

    def test_add_multiple_lessons(self, store) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        for i in range(3):
            meta = ApplicabilityMeta(
                created_at=f"2026-03-13T1{i}:00:00Z",
                generation=i + 1,
                best_score=0.5 + i * 0.1,
            )
            store.add_lesson("grid_ctf", f"- lesson {i}", meta)

        all_lessons = store.read_lessons("grid_ctf")
        assert len(all_lessons) == 3
        # IDs should be unique
        ids = {les.id for les in all_lessons}
        assert len(ids) == 3

    def test_lessons_json_file_location(self, store, tmp_path: Path) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=1,
            best_score=0.5,
        )
        store.add_lesson("grid_ctf", "- test", meta)
        expected_path = tmp_path / "knowledge" / "grid_ctf" / "lessons.json"
        assert expected_path.exists()
        data = json.loads(expected_path.read_text())
        assert isinstance(data, list)
        assert len(data) == 1


# ---------------------------------------------------------------------------
# 4. LessonStore — filtering (applicable, stale, superseded)
# ---------------------------------------------------------------------------


class TestLessonStoreFiltering:
    @pytest.fixture()
    def store_with_lessons(self, tmp_path: Path):
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        lessons = [
            # Fresh and applicable
            Lesson(
                id="L1",
                text="- fresh lesson",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=8,
                    best_score=0.85,
                    last_validated_gen=12,
                ),
            ),
            # Stale (not validated recently)
            Lesson(
                id="L2",
                text="- stale lesson",
                meta=ApplicabilityMeta(
                    created_at="2026-03-01T10:00:00Z",
                    generation=1,
                    best_score=0.5,
                    last_validated_gen=2,
                ),
            ),
            # Superseded
            Lesson(
                id="L3",
                text="- superseded lesson",
                meta=ApplicabilityMeta(
                    created_at="2026-03-05T10:00:00Z",
                    generation=4,
                    best_score=0.7,
                    last_validated_gen=10,
                    superseded_by="L1",
                ),
            ),
        ]
        store.write_lessons("grid_ctf", lessons)
        return store

    def test_get_applicable_lessons(self, store_with_lessons) -> None:
        applicable = store_with_lessons.get_applicable_lessons(
            "grid_ctf", current_generation=15, staleness_window=10,
        )
        assert len(applicable) == 1
        assert applicable[0].id == "L1"

    def test_get_stale_lessons(self, store_with_lessons) -> None:
        stale = store_with_lessons.get_stale_lessons(
            "grid_ctf", current_generation=15, staleness_window=10,
        )
        assert len(stale) == 1
        assert stale[0].id == "L2"

    def test_get_applicable_excludes_superseded(self, store_with_lessons) -> None:
        applicable = store_with_lessons.get_applicable_lessons(
            "grid_ctf", current_generation=15, staleness_window=10,
        )
        ids = {les.id for les in applicable}
        assert "L3" not in ids

    def test_all_applicable_when_everything_fresh(self, tmp_path: Path) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        lessons = [
            Lesson(
                id=f"L{i}",
                text=f"- lesson {i}",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=5,
                    best_score=0.8,
                    last_validated_gen=8,
                ),
            )
            for i in range(5)
        ]
        store.write_lessons("grid_ctf", lessons)
        applicable = store.get_applicable_lessons("grid_ctf", current_generation=10, staleness_window=10)
        assert len(applicable) == 5


# ---------------------------------------------------------------------------
# 5. LessonStore — invalidation
# ---------------------------------------------------------------------------


class TestLessonStoreInvalidation:
    @pytest.fixture()
    def store(self, tmp_path: Path):
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        lessons = [
            Lesson(
                id="L1",
                text="- schema-v1 lesson",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=3,
                    best_score=0.72,
                    schema_version="schema_v1",
                    last_validated_gen=5,
                ),
            ),
            Lesson(
                id="L2",
                text="- schema-v2 lesson",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T12:00:00Z",
                    generation=6,
                    best_score=0.85,
                    schema_version="schema_v2",
                    last_validated_gen=8,
                ),
            ),
        ]
        store.write_lessons("grid_ctf", lessons)
        return store

    def test_invalidate_by_schema_change(self, store) -> None:
        """Lessons from old schema are marked with last_validated_gen = -1 (force stale)."""
        invalidated = store.invalidate_by_schema_change("grid_ctf", new_schema_version="schema_v3")
        # Both L1 (schema_v1) and L2 (schema_v2) should be invalidated
        assert len(invalidated) == 2

        # Re-read and verify
        all_lessons = store.read_lessons("grid_ctf")
        for lesson in all_lessons:
            assert lesson.meta.last_validated_gen == -1

    def test_invalidate_preserves_matching_schema(self, store) -> None:
        """Lessons already on current schema are not invalidated."""
        invalidated = store.invalidate_by_schema_change("grid_ctf", new_schema_version="schema_v2")
        # Only L1 (schema_v1) should be invalidated
        assert len(invalidated) == 1
        assert invalidated[0].id == "L1"

        all_lessons = store.read_lessons("grid_ctf")
        found_l1 = next(les for les in all_lessons if les.id == "L1")
        found_l2 = next(les for les in all_lessons if les.id == "L2")
        assert found_l1.meta.last_validated_gen == -1
        assert found_l2.meta.last_validated_gen == 8  # unchanged

    def test_supersede_lesson(self, store) -> None:
        store.supersede_lesson("grid_ctf", old_id="L1", new_id="L2")
        all_lessons = store.read_lessons("grid_ctf")
        found_l1 = next(les for les in all_lessons if les.id == "L1")
        assert found_l1.meta.superseded_by == "L2"

    def test_supersede_nonexistent_lesson_is_noop(self, store) -> None:
        """Superseding a lesson that doesn't exist should not error."""
        store.supersede_lesson("grid_ctf", old_id="NONEXISTENT", new_id="L2")
        # No error raised; existing lessons unchanged
        all_lessons = store.read_lessons("grid_ctf")
        assert len(all_lessons) == 2


# ---------------------------------------------------------------------------
# 6. LessonStore — backward-compatible migration from raw bullets
# ---------------------------------------------------------------------------


class TestLessonStoreMigration:
    def test_migrate_from_raw_bullets(self, tmp_path: Path) -> None:
        """LessonStore can ingest raw bullet strings from SKILL.md when no lessons.json exists."""
        from autocontext.knowledge.lessons import LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        raw_bullets = [
            "- Aggressive openings outperform passive ones",
            "- Control center squares early",
            "- Avoid overextending flanks",
        ]
        migrated = store.migrate_from_raw_bullets("grid_ctf", raw_bullets, generation=5, best_score=0.8)
        assert len(migrated) == 3
        for lesson in migrated:
            assert lesson.id  # non-empty
            assert lesson.meta.generation == 5
            assert lesson.meta.best_score == 0.8
            assert lesson.meta.operation_type == "migration"

        # Verify persisted
        all_lessons = store.read_lessons("grid_ctf")
        assert len(all_lessons) == 3

    def test_migrate_skips_if_lessons_json_exists(self, tmp_path: Path) -> None:
        """Migration is idempotent — if lessons.json already exists, don't re-migrate."""
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        # Pre-populate lessons.json
        existing = [
            Lesson(
                id="existing_1",
                text="- already migrated",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=3,
                    best_score=0.7,
                ),
            )
        ]
        store.write_lessons("grid_ctf", existing)

        # Attempt migration with different bullets
        migrated = store.migrate_from_raw_bullets(
            "grid_ctf", ["- new bullet"], generation=5, best_score=0.8,
        )
        assert migrated == []  # No migration happened

        # Existing data unchanged
        all_lessons = store.read_lessons("grid_ctf")
        assert len(all_lessons) == 1
        assert all_lessons[0].id == "existing_1"


# ---------------------------------------------------------------------------
# 7. LessonStore — staleness report
# ---------------------------------------------------------------------------


class TestStalenessReport:
    def test_staleness_report_content(self, tmp_path: Path) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        lessons = [
            Lesson(
                id="L1",
                text="- fresh",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=8,
                    best_score=0.85,
                    last_validated_gen=14,
                ),
            ),
            Lesson(
                id="L2",
                text="- stale one",
                meta=ApplicabilityMeta(
                    created_at="2026-03-01T10:00:00Z",
                    generation=1,
                    best_score=0.5,
                    last_validated_gen=2,
                ),
            ),
            Lesson(
                id="L3",
                text="- superseded one",
                meta=ApplicabilityMeta(
                    created_at="2026-03-05T10:00:00Z",
                    generation=4,
                    best_score=0.7,
                    last_validated_gen=10,
                    superseded_by="L1",
                ),
            ),
        ]
        store.write_lessons("grid_ctf", lessons)

        report = store.staleness_report("grid_ctf", current_generation=15, staleness_window=10)
        assert isinstance(report, str)
        assert "stale" in report.lower() or "Stale" in report
        assert "L2" in report
        assert "superseded" in report.lower()
        assert "L3" in report
        # Fresh lessons should show as applicable
        assert "1 applicable" in report.lower() or "applicable: 1" in report.lower()

    def test_staleness_report_empty_when_all_fresh(self, tmp_path: Path) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        lessons = [
            Lesson(
                id="L1",
                text="- fresh",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=8,
                    best_score=0.85,
                    last_validated_gen=14,
                ),
            ),
        ]
        store.write_lessons("grid_ctf", lessons)
        report = store.staleness_report("grid_ctf", current_generation=15, staleness_window=10)
        assert "stale" not in report.lower() or "0 stale" in report.lower()

    def test_staleness_report_empty_scenario(self, tmp_path: Path) -> None:
        from autocontext.knowledge.lessons import LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)
        report = store.staleness_report("grid_ctf", current_generation=15)
        assert isinstance(report, str)


# ---------------------------------------------------------------------------
# 8. LessonStore — validate_lesson (refresh last_validated_gen)
# ---------------------------------------------------------------------------


class TestLessonValidation:
    def test_validate_lesson_refreshes_gen(self, tmp_path: Path) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        lessons = [
            Lesson(
                id="L1",
                text="- to validate",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=1,
                    best_score=0.5,
                    last_validated_gen=1,
                ),
            ),
        ]
        store.write_lessons("grid_ctf", lessons)

        store.validate_lesson("grid_ctf", "L1", current_generation=10)

        updated = store.read_lessons("grid_ctf")
        assert updated[0].meta.last_validated_gen == 10

    def test_validate_nonexistent_is_noop(self, tmp_path: Path) -> None:
        from autocontext.knowledge.lessons import LessonStore

        knowledge = tmp_path / "knowledge"
        skills = tmp_path / "skills"
        knowledge.mkdir()
        skills.mkdir()
        store = LessonStore(knowledge_root=knowledge, skills_root=skills)

        # Should not raise
        store.validate_lesson("grid_ctf", "NONEXISTENT", current_generation=10)


# ---------------------------------------------------------------------------
# 9. SessionReport integration — stale lesson counts
# ---------------------------------------------------------------------------


class TestSessionReportStaleLessons:
    def test_session_report_includes_stale_count(self) -> None:
        from autocontext.knowledge.report import SessionReport

        report = SessionReport(
            run_id="test_run",
            scenario="grid_ctf",
            start_score=0.5,
            end_score=0.85,
            start_elo=1000.0,
            end_elo=1200.0,
            total_generations=10,
            duration_seconds=120.0,
            stale_lessons_count=3,
            superseded_lessons_count=1,
        )
        assert report.stale_lessons_count == 3
        assert report.superseded_lessons_count == 1

    def test_session_report_markdown_includes_lesson_health(self) -> None:
        from autocontext.knowledge.report import SessionReport

        report = SessionReport(
            run_id="test_run",
            scenario="grid_ctf",
            start_score=0.5,
            end_score=0.85,
            start_elo=1000.0,
            end_elo=1200.0,
            total_generations=10,
            duration_seconds=120.0,
            stale_lessons_count=3,
            superseded_lessons_count=1,
        )
        md = report.to_markdown()
        assert "stale" in md.lower() or "Stale" in md
        assert "3" in md

    def test_session_report_defaults_zero(self) -> None:
        from autocontext.knowledge.report import SessionReport

        report = SessionReport(
            run_id="test_run",
            scenario="grid_ctf",
            start_score=0.5,
            end_score=0.85,
            start_elo=1000.0,
            end_elo=1200.0,
            total_generations=10,
            duration_seconds=120.0,
        )
        assert report.stale_lessons_count == 0
        assert report.superseded_lessons_count == 0


# ---------------------------------------------------------------------------
# 10. ArtifactStore integration — lesson_store property
# ---------------------------------------------------------------------------


class TestArtifactStoreLessonIntegration:
    @pytest.fixture()
    def artifact_store(self, tmp_path: Path):
        from autocontext.storage.artifacts import ArtifactStore

        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_artifact_store_has_lesson_store(self, artifact_store) -> None:
        from autocontext.knowledge.lessons import LessonStore

        ls = artifact_store.lesson_store
        assert isinstance(ls, LessonStore)

    def test_artifact_store_lesson_store_uses_same_roots(self, artifact_store, tmp_path: Path) -> None:
        ls = artifact_store.lesson_store
        assert ls.knowledge_root == tmp_path / "knowledge"
        assert ls.skills_root == tmp_path / "skills"

    def test_add_structured_lesson_via_artifact_store(self, artifact_store) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        meta = ApplicabilityMeta(
            created_at="2026-03-13T10:00:00Z",
            generation=3,
            best_score=0.72,
        )
        lesson = artifact_store.lesson_store.add_lesson("grid_ctf", "- test lesson", meta)
        assert lesson.id
        lessons = artifact_store.lesson_store.read_lessons("grid_ctf")
        assert len(lessons) == 1

    def test_read_skills_prefers_applicable_structured_lessons(self, artifact_store) -> None:
        from autocontext.knowledge.lessons import ApplicabilityMeta

        artifact_store.lesson_store.add_lesson(
            "grid_ctf",
            "- still valid",
            ApplicabilityMeta(
                created_at="2026-03-13T10:00:00Z",
                generation=4,
                best_score=0.72,
                last_validated_gen=4,
            ),
        )
        artifact_store.lesson_store.add_lesson(
            "grid_ctf",
            "- invalidated by schema change",
            ApplicabilityMeta(
                created_at="2026-03-13T11:00:00Z",
                generation=4,
                best_score=0.72,
                last_validated_gen=-1,
            ),
        )

        skills = artifact_store.read_skills("grid_ctf")
        assert "- still valid" in skills
        assert "- invalidated by schema change" not in skills
