"""Tests for AC-235: Append-only context mutation log and replay from last-known-good state.

Verifies:
1. MutationEntry construction and serialization.
2. MutationLog append/read operations (JSONL-backed).
3. Checkpoint creation and retrieval.
4. Replay from last-known-good checkpoint.
5. Log bounding / truncation.
6. ArtifactStore integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. MutationEntry
# ---------------------------------------------------------------------------


class TestMutationEntry:
    def test_construction_minimal(self) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        entry = MutationEntry(
            mutation_type="lesson_added",
            generation=3,
            payload={"lesson_id": "L1", "text": "- new lesson"},
        )
        assert entry.mutation_type == "lesson_added"
        assert entry.generation == 3
        assert entry.payload == {"lesson_id": "L1", "text": "- new lesson"}
        assert entry.timestamp  # auto-populated
        assert entry.run_id == ""
        assert entry.description == ""

    def test_construction_full(self) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        entry = MutationEntry(
            mutation_type="playbook_updated",
            generation=5,
            payload={"old_hash": "abc", "new_hash": "def"},
            timestamp="2026-03-13T10:00:00Z",
            run_id="run_123",
            description="Coach updated playbook after advance",
        )
        assert entry.mutation_type == "playbook_updated"
        assert entry.timestamp == "2026-03-13T10:00:00Z"
        assert entry.run_id == "run_123"
        assert entry.description == "Coach updated playbook after advance"

    def test_to_dict_from_dict_roundtrip(self) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        entry = MutationEntry(
            mutation_type="schema_change",
            generation=7,
            payload={"old_version": "v1", "new_version": "v2"},
            timestamp="2026-03-13T12:00:00Z",
            run_id="run_456",
            description="Schema migration applied",
        )
        d = entry.to_dict()
        assert isinstance(d, dict)
        restored = MutationEntry.from_dict(d)
        assert restored.mutation_type == entry.mutation_type
        assert restored.generation == entry.generation
        assert restored.payload == entry.payload
        assert restored.timestamp == entry.timestamp
        assert restored.run_id == entry.run_id
        assert restored.description == entry.description

    def test_known_mutation_types(self) -> None:
        """Verify all documented mutation types are accepted."""
        from autocontext.knowledge.mutation_log import MUTATION_TYPES, MutationEntry

        for mtype in MUTATION_TYPES:
            entry = MutationEntry(mutation_type=mtype, generation=1, payload={})
            assert entry.mutation_type == mtype


# ---------------------------------------------------------------------------
# 2. MutationLog — append/read
# ---------------------------------------------------------------------------


class TestMutationLogAppendRead:
    @pytest.fixture()
    def log(self, tmp_path: Path):
        from autocontext.knowledge.mutation_log import MutationLog

        return MutationLog(knowledge_root=tmp_path / "knowledge")

    def test_read_empty(self, log) -> None:
        entries = log.read("grid_ctf")
        assert entries == []

    def test_append_and_read(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        entry = MutationEntry(
            mutation_type="lesson_added",
            generation=1,
            payload={"text": "- first lesson"},
        )
        log.append("grid_ctf", entry)
        entries = log.read("grid_ctf")
        assert len(entries) == 1
        assert entries[0].mutation_type == "lesson_added"
        assert entries[0].payload == {"text": "- first lesson"}

    def test_append_multiple(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        for i in range(5):
            log.append(
                "grid_ctf",
                MutationEntry(
                    mutation_type="run_outcome",
                    generation=i + 1,
                    payload={"decision": "advance"},
                ),
            )
        entries = log.read("grid_ctf")
        assert len(entries) == 5
        assert entries[0].generation == 1
        assert entries[4].generation == 5

    def test_append_is_truly_append_only(self, log, tmp_path: Path) -> None:
        """Appending should not overwrite previous entries."""
        from autocontext.knowledge.mutation_log import MutationEntry

        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={"n": 1}),
        )
        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=2, payload={"n": 2}),
        )
        entries = log.read("grid_ctf")
        assert len(entries) == 2
        assert entries[0].payload == {"n": 1}
        assert entries[1].payload == {"n": 2}

    def test_jsonl_file_location(self, log, tmp_path: Path) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={}),
        )
        expected = tmp_path / "knowledge" / "grid_ctf" / "mutation_log.jsonl"
        assert expected.exists()

    def test_scenarios_isolated(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={"s": "ctf"}),
        )
        log.append(
            "othello",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={"s": "oth"}),
        )
        assert len(log.read("grid_ctf")) == 1
        assert len(log.read("othello")) == 1
        assert log.read("grid_ctf")[0].payload == {"s": "ctf"}


# ---------------------------------------------------------------------------
# 3. Checkpoint creation and retrieval
# ---------------------------------------------------------------------------


class TestCheckpoints:
    @pytest.fixture()
    def log(self, tmp_path: Path):
        from autocontext.knowledge.mutation_log import MutationLog

        return MutationLog(knowledge_root=tmp_path / "knowledge")

    def test_create_checkpoint(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        # Add some mutations first
        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={}),
        )
        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="playbook_updated", generation=2, payload={}),
        )

        checkpoint = log.create_checkpoint("grid_ctf", generation=2, run_id="run_1")
        assert checkpoint.generation == 2
        assert checkpoint.run_id == "run_1"
        assert checkpoint.entry_index >= 0  # index into the log

    def test_checkpoint_is_recorded_as_entry(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={}),
        )
        log.create_checkpoint("grid_ctf", generation=1, run_id="run_1")
        entries = log.read("grid_ctf")
        # The checkpoint itself is recorded as a mutation entry
        checkpoint_entries = [e for e in entries if e.mutation_type == "checkpoint"]
        assert len(checkpoint_entries) == 1

    def test_get_last_checkpoint(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={}),
        )
        log.create_checkpoint("grid_ctf", generation=1, run_id="run_1")
        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=3, payload={}),
        )
        log.create_checkpoint("grid_ctf", generation=3, run_id="run_2")
        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="playbook_updated", generation=4, payload={}),
        )

        last = log.get_last_checkpoint("grid_ctf")
        assert last is not None
        assert last.generation == 3
        assert last.run_id == "run_2"

    def test_get_last_checkpoint_none_when_no_checkpoints(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={}),
        )
        assert log.get_last_checkpoint("grid_ctf") is None

    def test_get_last_checkpoint_empty_log(self, log) -> None:
        assert log.get_last_checkpoint("grid_ctf") is None


# ---------------------------------------------------------------------------
# 4. Replay from checkpoint
# ---------------------------------------------------------------------------


class TestReplay:
    @pytest.fixture()
    def log_with_data(self, tmp_path: Path):
        from autocontext.knowledge.mutation_log import MutationEntry, MutationLog

        mlog = MutationLog(knowledge_root=tmp_path / "knowledge")

        # Pre-checkpoint mutations
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={"n": 1}),
        )
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="playbook_updated", generation=2, payload={"n": 2}),
        )
        mlog.create_checkpoint("grid_ctf", generation=2, run_id="run_1")

        # Post-checkpoint mutations
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=3, payload={"n": 3}),
        )
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="schema_change", generation=4, payload={"n": 4}),
        )
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="run_outcome", generation=5, payload={"n": 5}),
        )
        return mlog

    def test_replay_after_checkpoint(self, log_with_data) -> None:
        replayed = log_with_data.replay_after_checkpoint("grid_ctf")
        # Should only include post-checkpoint mutations (excluding the checkpoint entry itself)
        assert len(replayed) == 3
        assert replayed[0].mutation_type == "lesson_added"
        assert replayed[0].payload == {"n": 3}
        assert replayed[2].mutation_type == "run_outcome"

    def test_replay_returns_all_when_no_checkpoint(self, tmp_path: Path) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry, MutationLog

        mlog = MutationLog(knowledge_root=tmp_path / "knowledge")
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={"n": 1}),
        )
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=2, payload={"n": 2}),
        )
        replayed = mlog.replay_after_checkpoint("grid_ctf")
        assert len(replayed) == 2

    def test_replay_empty_log(self, tmp_path: Path) -> None:
        from autocontext.knowledge.mutation_log import MutationLog

        mlog = MutationLog(knowledge_root=tmp_path / "knowledge")
        replayed = mlog.replay_after_checkpoint("grid_ctf")
        assert replayed == []

    def test_replay_by_type(self, log_with_data) -> None:
        """Filter replayed mutations by type."""
        replayed = log_with_data.replay_after_checkpoint(
            "grid_ctf", mutation_types=["lesson_added"],
        )
        assert len(replayed) == 1
        assert replayed[0].payload == {"n": 3}

    def test_replay_by_multiple_types(self, log_with_data) -> None:
        replayed = log_with_data.replay_after_checkpoint(
            "grid_ctf", mutation_types=["lesson_added", "schema_change"],
        )
        assert len(replayed) == 2


# ---------------------------------------------------------------------------
# 5. Log bounding / truncation
# ---------------------------------------------------------------------------


class TestLogBounding:
    @pytest.fixture()
    def log(self, tmp_path: Path):
        from autocontext.knowledge.mutation_log import MutationLog

        return MutationLog(knowledge_root=tmp_path / "knowledge", max_entries=10)

    def test_truncate_preserves_recent(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        for i in range(15):
            log.append(
                "grid_ctf",
                MutationEntry(mutation_type="run_outcome", generation=i + 1, payload={"i": i}),
            )

        log.truncate("grid_ctf")
        entries = log.read("grid_ctf")
        assert len(entries) <= 10
        # Most recent entries preserved
        assert entries[-1].payload == {"i": 14}

    def test_truncate_preserves_last_checkpoint(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        for i in range(8):
            log.append(
                "grid_ctf",
                MutationEntry(mutation_type="run_outcome", generation=i + 1, payload={"i": i}),
            )
        log.create_checkpoint("grid_ctf", generation=8, run_id="run_1")
        for i in range(8, 13):
            log.append(
                "grid_ctf",
                MutationEntry(mutation_type="run_outcome", generation=i + 1, payload={"i": i}),
            )

        log.truncate("grid_ctf")
        entries = log.read("grid_ctf")
        # A recent checkpoint should be preserved when it still fits inside the bound.
        checkpoint_entries = [e for e in entries if e.mutation_type == "checkpoint"]
        assert len(checkpoint_entries) >= 1

    def test_truncate_noop_when_under_limit(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        for i in range(3):
            log.append(
                "grid_ctf",
                MutationEntry(mutation_type="run_outcome", generation=i + 1, payload={"i": i}),
            )
        log.truncate("grid_ctf")
        assert len(log.read("grid_ctf")) == 3

    def test_append_enforces_bound_automatically(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        for i in range(12):
            log.append(
                "grid_ctf",
                MutationEntry(mutation_type="run_outcome", generation=i + 1, payload={"i": i}),
            )

        entries = log.read("grid_ctf")
        assert len(entries) <= 10

    def test_truncate_drops_old_checkpoint_when_needed_to_enforce_bound(self, log) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        for i in range(5):
            log.append(
                "grid_ctf",
                MutationEntry(mutation_type="run_outcome", generation=i + 1, payload={"i": i}),
            )
        log.create_checkpoint("grid_ctf", generation=5, run_id="run_1")
        for i in range(5, 16):
            log.append(
                "grid_ctf",
                MutationEntry(mutation_type="run_outcome", generation=i + 1, payload={"i": i}),
            )

        entries = log.read("grid_ctf")
        assert len(entries) <= 10


# ---------------------------------------------------------------------------
# 6. Audit / query helpers
# ---------------------------------------------------------------------------


class TestAuditHelpers:
    @pytest.fixture()
    def log(self, tmp_path: Path):
        from autocontext.knowledge.mutation_log import MutationEntry, MutationLog

        mlog = MutationLog(knowledge_root=tmp_path / "knowledge")
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={"id": "L1"}),
        )
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="playbook_updated", generation=2, payload={"hash": "abc"}),
        )
        mlog.append(
            "grid_ctf",
            MutationEntry(
                mutation_type="lesson_removed", generation=3, payload={"id": "L1"},
                description="Curator removed stale lesson",
            ),
        )
        mlog.append(
            "grid_ctf",
            MutationEntry(mutation_type="run_outcome", generation=3, payload={"decision": "advance"}),
        )
        return mlog

    def test_filter_by_type(self, log) -> None:
        entries = log.read("grid_ctf", mutation_types=["lesson_added", "lesson_removed"])
        assert len(entries) == 2
        assert entries[0].mutation_type == "lesson_added"
        assert entries[1].mutation_type == "lesson_removed"

    def test_filter_by_generation_range(self, log) -> None:
        entries = log.read("grid_ctf", min_generation=2, max_generation=3)
        assert len(entries) == 3
        assert all(2 <= e.generation <= 3 for e in entries)

    def test_audit_summary(self, log) -> None:
        """Generate a human-readable summary of mutations."""
        summary = log.audit_summary("grid_ctf")
        assert isinstance(summary, str)
        assert "lesson_added" in summary
        assert "playbook_updated" in summary
        assert "4" in summary or "total" in summary.lower()  # total count


# ---------------------------------------------------------------------------
# 7. ArtifactStore integration
# ---------------------------------------------------------------------------


class TestArtifactStoreIntegration:
    @pytest.fixture()
    def artifact_store(self, tmp_path: Path):
        from autocontext.storage.artifacts import ArtifactStore

        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_artifact_store_has_mutation_log(self, artifact_store) -> None:
        from autocontext.knowledge.mutation_log import MutationLog

        mlog = artifact_store.mutation_log
        assert isinstance(mlog, MutationLog)

    def test_mutation_log_uses_knowledge_root(self, artifact_store, tmp_path: Path) -> None:
        assert artifact_store.mutation_log.knowledge_root == tmp_path / "knowledge"

    def test_append_via_artifact_store(self, artifact_store) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        artifact_store.mutation_log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={}),
        )
        assert len(artifact_store.mutation_log.read("grid_ctf")) == 1

    def test_write_playbook_logs_mutation(self, artifact_store) -> None:
        artifact_store.write_playbook("grid_ctf", "# Playbook\nUse center control.\n")
        entries = artifact_store.mutation_log.read("grid_ctf", mutation_types=["playbook_updated"])
        assert len(entries) == 1
        assert entries[0].mutation_type == "playbook_updated"

    def test_write_notebook_logs_mutation(self, artifact_store) -> None:
        artifact_store.write_notebook(
            "session_1",
            {"scenario_name": "grid_ctf", "current_objective": "Test objective"},
        )
        entries = artifact_store.mutation_log.read("grid_ctf", mutation_types=["notebook_updated"])
        assert len(entries) == 1
        assert entries[0].payload["session_id"] == "session_1"

    def test_read_mutation_replay_uses_post_checkpoint_entries(self, artifact_store) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry

        artifact_store.mutation_log.append(
            "grid_ctf",
            MutationEntry(mutation_type="lesson_added", generation=1, payload={"id": "L1"}),
        )
        artifact_store.mutation_log.create_checkpoint("grid_ctf", generation=1, run_id="run_1")
        artifact_store.mutation_log.append(
            "grid_ctf",
            MutationEntry(
                mutation_type="playbook_updated",
                generation=2,
                payload={"id": "pb"},
                description="Playbook updated",
            ),
        )

        summary = artifact_store.read_mutation_replay("grid_ctf")
        assert "Context mutations since last checkpoint" in summary
        assert "playbook_updated" in summary
