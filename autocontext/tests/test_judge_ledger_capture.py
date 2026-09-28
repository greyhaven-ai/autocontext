# autocontext/tests/test_judge_ledger_capture.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from autocontext.config import load_settings
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.prescreen.ledger import JudgeLedger, ledger_for, text_hash
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


class ScriptedTask(AgentTaskInterface):
    """Scores follow a script; each revision appends a marker so outputs differ."""

    def __init__(self, scores: list[float], revise_suffix: str = " v") -> None:
        self.scores = scores
        self.revise_suffix = revise_suffix
        self.evaluations = 0

    def get_task_prompt(self, state: dict) -> str:
        return "write the thing"

    def get_rubric(self) -> str:
        return "be good"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "write the thing"

    def evaluate_output(
        self,
        output,
        state,
        reference_context=None,
        required_concepts=None,  # type: ignore[no-untyped-def]
        calibration_examples=None,
        pinned_dimensions=None,
    ):
        score = self.scores[min(self.evaluations, len(self.scores) - 1)]
        self.evaluations += 1
        return AgentTaskResult(
            score=score, reasoning=f"score {score}", dimension_scores={"quality": score}, evaluator_epoch="epoch-a"
        )

    def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
        return output + self.revise_suffix + str(self.evaluations)


class ListStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def insert_judge_ledger_row(self, values: dict[str, Any]) -> int:
        self.rows.append(dict(values))
        return len(self.rows)


def _ledger(store: Any) -> JudgeLedger:
    return JudgeLedger(store, run_id="run-1", scenario_name="demo", scenario_family="agent_task")


def test_one_row_per_real_judge_call_with_previous_round_links() -> None:
    store = ListStore()
    ledger = _ledger(store)
    result = ImprovementLoop(ScriptedTask([0.3, 0.5, 0.95]), max_rounds=5, judge_ledger=ledger).run("draft", {})
    assert result.met_threshold
    assert [r["round_number"] for r in store.rows] == [1, 2, 3]
    assert [r["passed"] for r in store.rows] == [0, 0, 1]
    assert [r["previous_round_number"] for r in store.rows] == [None, 1, 2]
    assert [r["previous_score"] for r in store.rows] == [None, 0.3, 0.5]
    assert {r["loop_id"] for r in store.rows} == {ledger.loop_id}
    first = store.rows[0]
    assert first["task_prompt"] == "write the thing"
    assert first["task_prompt_hash"] == text_hash("write the thing")
    assert first["rubric_hash"] == text_hash("be good")
    assert first["output"] == "draft" and first["output_hash"] == text_hash("draft")
    assert first["evaluator_epoch"] == "epoch-a" and first["max_rounds"] == 5 and first["quality_threshold"] == 0.9
    assert first["dimension_scores_json"] == '{"quality": 0.3}'


def test_capture_does_not_change_the_loop_result() -> None:
    plain = ImprovementLoop(ScriptedTask([0.3, 0.5, 0.95]), max_rounds=5).run("draft", {})
    captured = ImprovementLoop(ScriptedTask([0.3, 0.5, 0.95]), max_rounds=5, judge_ledger=_ledger(ListStore())).run("draft", {})
    fields = ("best_output", "best_score", "best_round", "total_rounds", "met_threshold", "termination_reason")
    assert [getattr(plain, f) for f in fields] == [getattr(captured, f) for f in fields]


def test_missing_target_rounds_are_not_recorded() -> None:
    store = ListStore()
    task = ScriptedTask([0.95], revise_suffix=" MUST ")
    ImprovementLoop(task, max_rounds=5, required_targets=["MUST"], judge_ledger=_ledger(store)).run("draft", {})
    assert task.evaluations == len(store.rows)
    assert store.rows and store.rows[0]["round_number"] == 2


def test_cache_replays_are_not_recorded() -> None:
    class SameOutput(ScriptedTask):
        def revise_output(self, output, judge_result, state):  # type: ignore[no-untyped-def]
            return output  # byte-identical, so later rounds replay the cached verdict

    store = ListStore()
    task = SameOutput([0.4])
    # NOTE (deviation from brief, see task-2-report.md): dimension pinning (AC-902) changes the
    # verifier-cache salt from round 1 (pinned_dimensions=None) to round 2 (pinned_dimensions=
    # ["quality"]), so round 2 is also a real judge call before the loop's own unchanged-output
    # guard stops it -- confirmed pre-existing on the pristine f5886c496 improvement_loop.py, with
    # no judge_ledger involved. The invariant this test protects (ledger rows == real judge calls,
    # so any cache replay is excluded) holds at 2, not the brief's literal 1.
    ImprovementLoop(task, max_rounds=5, judge_ledger=_ledger(store)).run("draft", {})
    assert len(store.rows) == task.evaluations == 2


def test_a_failing_store_never_breaks_the_loop(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenStore:
        def insert_judge_ledger_row(self, values: dict[str, Any]) -> int:
            raise RuntimeError("disk full")

    with caplog.at_level(logging.WARNING):
        result = ImprovementLoop(ScriptedTask([0.3, 0.95]), max_rounds=5, judge_ledger=_ledger(BrokenStore())).run("d", {})
    assert result.met_threshold
    assert "judge ledger write failed" in caplog.text


def test_rows_land_in_sqlite(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "ledger.db")
    store.migrate(MIGRATIONS)
    ImprovementLoop(ScriptedTask([0.3, 0.95]), max_rounds=5, judge_ledger=_ledger(store)).run("draft", {})
    assert [r["round_number"] for r in store.list_judge_ledger_rows()] == [1, 2]


def test_ledger_for_requires_the_setting_to_be_exactly_true(monkeypatch: pytest.MonkeyPatch) -> None:
    class Settings:
        judge_ledger_enabled: object = False

    settings = Settings()
    assert ledger_for(settings, ListStore(), run_id=None, scenario_name="s") is None  # type: ignore[arg-type]
    settings.judge_ledger_enabled = "yes"
    assert ledger_for(settings, ListStore(), run_id=None, scenario_name="s") is None  # type: ignore[arg-type]
    settings.judge_ledger_enabled = True
    ledger = ledger_for(settings, ListStore(), run_id="r", scenario_name="s")  # type: ignore[arg-type]
    assert isinstance(ledger, JudgeLedger) and ledger.scenario_family == "agent_task"


def test_setting_defaults_off_and_reads_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCONTEXT_JUDGE_LEDGER_ENABLED", raising=False)
    assert load_settings().judge_ledger_enabled is False
    monkeypatch.setenv("AUTOCONTEXT_JUDGE_LEDGER_ENABLED", "true")
    assert load_settings().judge_ledger_enabled is True
