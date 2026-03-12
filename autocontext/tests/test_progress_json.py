"""Tests for structured progress JSON snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from autocontext.knowledge.progress import ProgressSnapshot, build_progress_snapshot
from autocontext.scenarios.base import Observation
from autocontext.storage.artifacts import ArtifactStore

# ── ProgressSnapshot dataclass ──────────────────────────────────────────


class TestProgressSnapshot:
    def test_to_dict_roundtrip(self) -> None:
        snap = ProgressSnapshot(
            generation=3,
            best_score=0.75,
            best_elo=1050.0,
            mean_score=0.65,
            last_advance_generation=2,
            stagnation_count=1,
            gate_history=["advance", "advance", "rollback"],
            top_lessons=["lesson1", "lesson2"],
            blocked_approaches=[],
            strategy_summary={"aggression": 0.8},
            score_trend=[0.5, 0.6, 0.75],
        )
        d = snap.to_dict()
        restored = ProgressSnapshot.from_dict(d)
        assert restored == snap

    def test_to_json_valid(self) -> None:
        snap = ProgressSnapshot(
            generation=1,
            best_score=0.5,
            best_elo=1000.0,
            mean_score=0.45,
            last_advance_generation=1,
            stagnation_count=0,
            gate_history=["advance"],
            top_lessons=[],
            blocked_approaches=[],
            strategy_summary={},
            score_trend=[0.5],
        )
        parsed = json.loads(snap.to_json())
        assert parsed["generation"] == 1
        assert parsed["best_score"] == 0.5

    def test_to_json_sorted_keys(self) -> None:
        snap = ProgressSnapshot(
            generation=1,
            best_score=0.5,
            best_elo=1000.0,
            mean_score=0.45,
            last_advance_generation=0,
            stagnation_count=0,
            gate_history=[],
            top_lessons=[],
            blocked_approaches=[],
            strategy_summary={},
            score_trend=[],
        )
        text = snap.to_json()
        keys = list(json.loads(text).keys())
        assert keys == sorted(keys)


# ── build_progress_snapshot ─────────────────────────────────────────────


class TestBuildProgressSnapshot:
    def test_last_advance_generation(self) -> None:
        snap = build_progress_snapshot(
            generation=4,
            best_score=0.8,
            best_elo=1100.0,
            mean_score=0.7,
            gate_history=["advance", "rollback", "advance", "rollback"],
            score_history=[0.5, 0.6, 0.8, 0.7],
            current_strategy={"x": 1},
            lessons=["a", "b", "c"],
        )
        assert snap.last_advance_generation == 3  # 3rd entry (index 2) is last advance

    def test_stagnation_count_trailing_rollbacks(self) -> None:
        snap = build_progress_snapshot(
            generation=5,
            best_score=0.6,
            best_elo=1000.0,
            mean_score=0.55,
            gate_history=["advance", "rollback", "rollback", "rollback"],
            score_history=[0.6, 0.5, 0.5, 0.5],
            current_strategy={},
            lessons=[],
        )
        assert snap.stagnation_count == 3

    def test_stagnation_count_all_advance(self) -> None:
        snap = build_progress_snapshot(
            generation=3,
            best_score=0.9,
            best_elo=1200.0,
            mean_score=0.85,
            gate_history=["advance", "advance", "advance"],
            score_history=[0.7, 0.8, 0.9],
            current_strategy={},
            lessons=[],
        )
        assert snap.stagnation_count == 0

    def test_top_lessons_capped_at_5(self) -> None:
        lessons = [f"lesson_{i}" for i in range(10)]
        snap = build_progress_snapshot(
            generation=1,
            best_score=0.5,
            best_elo=1000.0,
            mean_score=0.45,
            gate_history=["advance"],
            score_history=[0.5],
            current_strategy={},
            lessons=lessons,
        )
        assert len(snap.top_lessons) == 5
        assert snap.top_lessons == lessons[:5]

    def test_score_trend_last_10(self) -> None:
        scores = list(range(20))
        snap = build_progress_snapshot(
            generation=20,
            best_score=19.0,
            best_elo=1500.0,
            mean_score=15.0,
            gate_history=[],
            score_history=[float(s) for s in scores],
            current_strategy={},
            lessons=[],
        )
        assert len(snap.score_trend) == 10
        assert snap.score_trend == [float(s) for s in range(10, 20)]

    def test_empty_gate_history(self) -> None:
        snap = build_progress_snapshot(
            generation=0,
            best_score=0.0,
            best_elo=1000.0,
            mean_score=0.0,
            gate_history=[],
            score_history=[],
            current_strategy={},
            lessons=[],
        )
        assert snap.last_advance_generation == 0
        assert snap.stagnation_count == 0

    def test_strategy_summary_is_copy(self) -> None:
        strategy = {"aggression": 0.5}
        snap = build_progress_snapshot(
            generation=1,
            best_score=0.5,
            best_elo=1000.0,
            mean_score=0.5,
            gate_history=[],
            score_history=[0.5],
            current_strategy=strategy,
            lessons=[],
        )
        assert snap.strategy_summary == strategy
        assert snap.strategy_summary is not strategy


# ── ArtifactStore write/read progress ───────────────────────────────────


class TestArtifactStoreProgress:
    def _make_store(self, tmp_path: Path) -> ArtifactStore:
        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_read_progress_missing(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        assert store.read_progress("test_scenario") is None

    def test_write_and_read_progress(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        data: dict[str, object] = {"generation": 1, "best_score": 0.5}
        store.write_progress("test_scenario", data)
        result = store.read_progress("test_scenario")
        assert result is not None
        assert result["generation"] == 1
        assert result["best_score"] == 0.5

    def test_write_progress_creates_directory(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        data: dict[str, object] = {"generation": 1}
        store.write_progress("new_scenario", data)
        path = tmp_path / "knowledge" / "new_scenario" / "progress.json"
        assert path.exists()

    def test_write_progress_overwrites(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.write_progress("s1", {"generation": 1, "best_score": 0.3})
        store.write_progress("s1", {"generation": 2, "best_score": 0.7})
        result = store.read_progress("s1")
        assert result is not None
        assert result["generation"] == 2
        assert result["best_score"] == 0.7


# ── Prompt bundle injection ─────────────────────────────────────────────


class TestPromptBundleProgressInjection:
    def _obs(self) -> Observation:
        return Observation(narrative="test", state={"key": "value"}, constraints=["c1"])

    def test_progress_json_included_in_prompt(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        progress = json.dumps({"generation": 3, "best_score": 0.8}, indent=2, sort_keys=True)
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=self._obs(),
            current_playbook="playbook",
            available_tools="tools",
            progress_json=progress,
        )
        assert "Progress snapshot:" in bundle.competitor
        assert '"best_score": 0.8' in bundle.competitor
        assert "Progress snapshot:" in bundle.analyst
        assert "Progress snapshot:" in bundle.coach

    def test_no_progress_json_when_empty(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=self._obs(),
            current_playbook="playbook",
            available_tools="tools",
            progress_json="",
        )
        assert "Progress snapshot:" not in bundle.competitor
        assert "Progress snapshot:" not in bundle.analyst

    def test_progress_json_default_empty(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=self._obs(),
            current_playbook="playbook",
            available_tools="tools",
        )
        assert "Progress snapshot:" not in bundle.competitor
