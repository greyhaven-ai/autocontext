"""Tests for human feedback storage and judge calibration."""

from __future__ import annotations

from pathlib import Path

import pytest

from mts.execution.judge import LLMJudge
from mts.storage.sqlite_store import SQLiteStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    db = SQLiteStore(tmp_path / "test.db")
    migrations = Path(__file__).resolve().parent.parent / "migrations"
    db.migrate(migrations)
    return db


class TestFeedbackStorage:
    def test_insert_and_get(self, store: SQLiteStore) -> None:
        row_id = store.insert_human_feedback(
            scenario_name="test_task",
            agent_output="some output",
            human_score=0.3,
            human_notes="missed the point",
        )
        assert row_id > 0

        items = store.get_human_feedback("test_task")
        assert len(items) == 1
        assert items[0]["human_score"] == 0.3
        assert items[0]["human_notes"] == "missed the point"
        assert items[0]["agent_output"] == "some output"

    def test_get_empty(self, store: SQLiteStore) -> None:
        items = store.get_human_feedback("nonexistent")
        assert items == []

    def test_multiple_feedback(self, store: SQLiteStore) -> None:
        store.insert_human_feedback("s1", "out1", human_score=0.2, human_notes="bad")
        store.insert_human_feedback("s1", "out2", human_score=0.8, human_notes="good")
        store.insert_human_feedback("s2", "out3", human_score=0.5, human_notes="ok")

        s1_items = store.get_human_feedback("s1")
        assert len(s1_items) == 2

        s2_items = store.get_human_feedback("s2")
        assert len(s2_items) == 1

    def test_calibration_examples_requires_score_and_notes(self, store: SQLiteStore) -> None:
        # Only score, no notes
        store.insert_human_feedback("s1", "out1", human_score=0.5, human_notes="")
        # Only notes, no score
        store.insert_human_feedback("s1", "out2", human_score=None, human_notes="some notes")
        # Both score and notes
        store.insert_human_feedback("s1", "out3", human_score=0.3, human_notes="bad output")

        calibration = store.get_calibration_examples("s1")
        assert len(calibration) == 1
        assert calibration[0]["human_score"] == 0.3

    def test_feedback_with_generation_id(self, store: SQLiteStore) -> None:
        store.insert_human_feedback(
            "s1", "out1", human_score=0.7, human_notes="decent", generation_id="gen_123"
        )
        items = store.get_human_feedback("s1")
        assert items[0]["generation_id"] == "gen_123"

    def test_feedback_limit(self, store: SQLiteStore) -> None:
        for i in range(20):
            store.insert_human_feedback("s1", f"out{i}", human_score=0.5, human_notes=f"note{i}")
        items = store.get_human_feedback("s1", limit=5)
        assert len(items) == 5

    def test_rejects_score_above_one(self, store: SQLiteStore) -> None:
        with pytest.raises(ValueError, match="must be in"):
            store.insert_human_feedback("s1", "out", human_score=1.5)

    def test_rejects_negative_score(self, store: SQLiteStore) -> None:
        with pytest.raises(ValueError, match="must be in"):
            store.insert_human_feedback("s1", "out", human_score=-0.1)

    def test_accepts_boundary_scores(self, store: SQLiteStore) -> None:
        store.insert_human_feedback("s1", "out1", human_score=0.0, human_notes="zero")
        store.insert_human_feedback("s1", "out2", human_score=1.0, human_notes="one")
        items = store.get_human_feedback("s1")
        assert len(items) == 2


class TestJudgeCalibration:
    def test_judge_with_calibration_examples(self) -> None:
        """Judge should include calibration in the prompt."""
        prompts_seen: list[str] = []

        def mock_llm(system: str, user: str) -> str:
            prompts_seen.append(user)
            return (
                '<!-- JUDGE_RESULT_START -->'
                '{"score": 0.5, "reasoning": "ok", "dimensions": {}}'
                '<!-- JUDGE_RESULT_END -->'
            )

        judge = LLMJudge(model="test", rubric="test rubric", llm_fn=mock_llm)
        calibration = [
            {"human_score": 0.2, "human_notes": "Wrong topic", "agent_output": "bad output here"},
            {"human_score": 0.9, "human_notes": "Excellent", "agent_output": "great output here"},
        ]
        judge.evaluate("task", "output", calibration_examples=calibration)

        assert len(prompts_seen) == 1
        prompt = prompts_seen[0]
        assert "Calibration Examples" in prompt
        assert "Score: 0.2" in prompt
        assert "Wrong topic" in prompt
        assert "Score: 0.9" in prompt
        assert "Excellent" in prompt

    def test_judge_without_calibration(self) -> None:
        """Judge should work fine without calibration (backward compat)."""
        def mock_llm(system: str, user: str) -> str:
            return (
                '<!-- JUDGE_RESULT_START -->'
                '{"score": 0.7, "reasoning": "good", "dimensions": {}}'
                '<!-- JUDGE_RESULT_END -->'
            )

        judge = LLMJudge(model="test", rubric="test rubric", llm_fn=mock_llm)
        result = judge.evaluate("task", "output")
        assert result.score == 0.7

    def test_judge_calibration_with_reference_context(self) -> None:
        """Calibration and reference context should work together."""
        prompts_seen: list[str] = []

        def mock_llm(system: str, user: str) -> str:
            prompts_seen.append(user)
            return (
                '<!-- JUDGE_RESULT_START -->'
                '{"score": 0.4, "reasoning": "inaccurate", "dimensions": {"factual_accuracy": 0.3}}'
                '<!-- JUDGE_RESULT_END -->'
            )

        judge = LLMJudge(model="test", rubric="test rubric", llm_fn=mock_llm)
        calibration = [{"human_score": 0.1, "human_notes": "Totally wrong", "agent_output": "wrong"}]
        result = judge.evaluate(
            "task",
            "output",
            reference_context="RLM means Recursive Language Model",
            calibration_examples=calibration,
        )

        prompt = prompts_seen[0]
        assert "Reference Context" in prompt
        assert "Calibration Examples" in prompt
        assert result.dimension_scores.get("factual_accuracy") == 0.3
