"""Tests for AC-171: match-level export with replay/state history.

Covers: MatchRecord enrichment, insert_match with replay, _iter_matches
with state history, export boundary tests, serialization.
"""

from __future__ import annotations

import json
from pathlib import Path


def _setup_db(tmp_path: Path) -> tuple:
    """Create a SQLiteStore with runs, generations, and match data."""
    from autocontext.storage.sqlite_store import SQLiteStore

    db_path = tmp_path / "test.db"
    sqlite = SQLiteStore(db_path)
    sqlite.migrate(Path("migrations"))

    sqlite.create_run("run-1", "grid_ctf", generations=3, executor_mode="local")
    sqlite.upsert_generation(
        "run-1", 1,
        mean_score=0.65, best_score=0.70, elo=1050.0,
        wins=3, losses=2, gate_decision="advance", status="completed",
    )
    sqlite.append_agent_output("run-1", 1, "competitor", '{"aggression": 0.8}')
    return sqlite, tmp_path


# ===========================================================================
# MatchRecord serialization (enriched with replay data)
# ===========================================================================


class TestMatchRecordEnriched:
    def test_new_fields(self) -> None:
        from autocontext.training.types import MatchRecord

        rec = MatchRecord(
            run_id="run-1",
            generation_index=1,
            seed=42,
            score=0.75,
            passed_validation=True,
            validation_errors="",
            winner="challenger",
            strategy='{"aggression": 0.8}',
            replay_json='[{"turn": 1, "action": "move_north"}]',
            states=[{"turn": 1, "grid": [[0]]}],
        )
        assert rec.winner == "challenger"
        assert len(rec.states) == 1

    def test_states_empty_when_not_available(self) -> None:
        from autocontext.training.types import MatchRecord

        rec = MatchRecord(
            run_id="run-1", generation_index=1, seed=42,
            score=0.5, passed_validation=True, validation_errors="",
        )
        assert rec.states == []
        assert rec.winner is None

    def test_to_dict_includes_all_fields(self) -> None:
        from autocontext.training.types import MatchRecord

        rec = MatchRecord(
            run_id="run-1", generation_index=1, seed=42,
            score=0.8, passed_validation=True, validation_errors="",
            winner="challenger", strategy='{"x": 1}',
            replay_json='[{"event": "win"}]',
            states=[{"turn": 1}],
        )
        d = rec.to_dict()
        assert d["winner"] == "challenger"
        assert d["states"] == [{"turn": 1}]
        assert "replay_json" in d

    def test_to_dict_without_optional_fields(self) -> None:
        from autocontext.training.types import MatchRecord

        rec = MatchRecord(
            run_id="run-1", generation_index=1, seed=42,
            score=0.5, passed_validation=True, validation_errors="",
        )
        d = rec.to_dict()
        assert d["winner"] is None
        assert d["states"] == []


# ===========================================================================
# insert_match with replay data (persistence boundary)
# ===========================================================================


class TestInsertMatchWithReplay:
    def test_insert_and_read_with_replay(self, tmp_path: Path) -> None:
        sqlite, _ = _setup_db(tmp_path)

        replay = [{"turn": 1, "action": "capture_flag"}]
        sqlite.insert_match(
            "run-1", 1, seed=42, score=0.80,
            passed_validation=True, validation_errors="",
            winner="challenger",
            strategy_json='{"aggression": 0.8}',
            replay_json=json.dumps(replay),
        )

        matches = sqlite.get_matches_for_run("run-1")
        assert len(matches) == 1
        m = matches[0]
        assert m["winner"] == "challenger"
        assert m["strategy_json"] == '{"aggression": 0.8}'
        assert "capture_flag" in m["replay_json"]

    def test_insert_without_replay_still_works(self, tmp_path: Path) -> None:
        """Backward compat: insert without replay columns should still work."""
        sqlite, _ = _setup_db(tmp_path)

        sqlite.insert_match(
            "run-1", 1, seed=99, score=0.50,
            passed_validation=True, validation_errors="",
        )

        matches = sqlite.get_matches_for_run("run-1")
        assert len(matches) == 1
        assert matches[0]["winner"] is None or matches[0]["winner"] == ""


# ===========================================================================
# Export boundary: _iter_matches yields enriched MatchRecords
# ===========================================================================


class TestExportMatchRecords:
    def test_match_records_yielded_when_enabled(self, tmp_path: Path) -> None:
        from autocontext.storage.artifacts import ArtifactStore
        from autocontext.training.export import export_training_data
        from autocontext.training.types import MatchRecord

        sqlite, _ = _setup_db(tmp_path)
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

        sqlite.insert_match(
            "run-1", 1, seed=42, score=0.80,
            passed_validation=True, validation_errors="",
            winner="challenger",
            strategy_json='{"x": 1}',
            replay_json='[{"turn": 1}]',
        )

        records = list(export_training_data(sqlite, artifacts, run_id="run-1", include_matches=True))
        match_records = [r for r in records if isinstance(r, MatchRecord)]
        assert len(match_records) == 1
        assert match_records[0].winner == "challenger"

    def test_match_records_not_yielded_when_disabled(self, tmp_path: Path) -> None:
        from autocontext.storage.artifacts import ArtifactStore
        from autocontext.training.export import export_training_data
        from autocontext.training.types import MatchRecord

        sqlite, _ = _setup_db(tmp_path)
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

        sqlite.insert_match("run-1", 1, seed=42, score=0.80, passed_validation=True, validation_errors="")

        records = list(export_training_data(sqlite, artifacts, run_id="run-1", include_matches=False))
        match_records = [r for r in records if isinstance(r, MatchRecord)]
        assert len(match_records) == 0

    def test_state_history_from_replay(self, tmp_path: Path) -> None:
        from autocontext.storage.artifacts import ArtifactStore
        from autocontext.training.export import export_training_data
        from autocontext.training.types import MatchRecord

        sqlite, _ = _setup_db(tmp_path)
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

        replay_with_states = json.dumps([
            {"turn": 1, "state": {"grid": [[1, 0]]}},
            {"turn": 2, "state": {"grid": [[1, 1]]}},
        ])
        sqlite.insert_match(
            "run-1", 1, seed=42, score=0.80,
            passed_validation=True, validation_errors="",
            replay_json=replay_with_states,
        )

        records = list(export_training_data(sqlite, artifacts, run_id="run-1", include_matches=True))
        match_records = [r for r in records if isinstance(r, MatchRecord)]
        assert len(match_records) == 1
        assert len(match_records[0].states) == 2

    def test_state_history_empty_when_no_states_in_replay(self, tmp_path: Path) -> None:
        from autocontext.storage.artifacts import ArtifactStore
        from autocontext.training.export import export_training_data
        from autocontext.training.types import MatchRecord

        sqlite, _ = _setup_db(tmp_path)
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

        replay_no_states = json.dumps([{"turn": 1, "action": "move"}])
        sqlite.insert_match(
            "run-1", 1, seed=42, score=0.60,
            passed_validation=True, validation_errors="",
            replay_json=replay_no_states,
        )

        records = list(export_training_data(sqlite, artifacts, run_id="run-1", include_matches=True))
        match_records = [r for r in records if isinstance(r, MatchRecord)]
        assert len(match_records) == 1
        assert match_records[0].states == []
