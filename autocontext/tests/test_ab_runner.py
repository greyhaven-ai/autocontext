"""Tests for A/B testing runner."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from autocontext.evaluation.ab_runner import ABTestConfig, ABTestResult, ABTestRunner
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def test_ab_config_fields() -> None:
    config = ABTestConfig(
        scenario="grid_ctf",
        baseline_env={"AUTOCONTEXT_RLM_ENABLED": "false"},
        treatment_env={"AUTOCONTEXT_RLM_ENABLED": "true"},
        runs_per_condition=3,
        generations_per_run=2,
    )
    assert config.scenario == "grid_ctf"
    assert config.runs_per_condition == 3


def test_ab_result_computes_delta() -> None:
    result = ABTestResult(
        baseline_scores=[0.3, 0.4, 0.35],
        treatment_scores=[0.5, 0.6, 0.55],
    )
    assert result.mean_delta() > 0
    assert result.treatment_wins() == 3
    assert result.baseline_wins() == 0


def test_ab_result_empty_returns_zero() -> None:
    result = ABTestResult()
    assert result.mean_delta() == 0.0
    assert result.treatment_wins() == 0
    assert result.baseline_wins() == 0


def test_ab_result_ties() -> None:
    result = ABTestResult(
        baseline_scores=[0.5, 0.5],
        treatment_scores=[0.5, 0.5],
    )
    assert result.mean_delta() == 0.0
    assert result.treatment_wins() == 0
    assert result.baseline_wins() == 0


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in [key for key in os.environ if key.startswith("AUTOCONTEXT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
    monkeypatch.setenv("AUTOCONTEXT_MATCHES_PER_GENERATION", "1")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _migrated_store(workspace: Path) -> SQLiteStore:
    store = SQLiteStore(workspace / "runs" / "autocontext.sqlite3")
    store.migrate(MIGRATIONS_DIR)
    return store


def _config(scenario: str, **overrides: str) -> ABTestConfig:
    return ABTestConfig(
        scenario=scenario,
        baseline_env={},
        treatment_env={},
        runs_per_condition=1,
        generations_per_run=1,
        **overrides,
    )


def _ab_runs(workspace: Path) -> dict[str, tuple[str, int]]:
    """Map each ab_* run id to its scenario and its number of generation rows."""
    conn = sqlite3.connect(workspace / "runs" / "autocontext.sqlite3")
    try:
        rows = conn.execute(
            "SELECT r.run_id, r.scenario, COUNT(g.generation_index) FROM runs r "
            "LEFT JOIN generations g ON g.run_id = r.run_id WHERE r.run_id LIKE 'ab_%' GROUP BY r.run_id"
        ).fetchall()
    finally:
        conn.close()
    return {run_id: (scenario, gens) for run_id, scenario, gens in rows}


@pytest.mark.slow
def test_rerun_records_new_arm_runs_instead_of_reusing_the_first_rows(workspace: Path) -> None:
    """Fixed arm run ids made a second A/B test re-enter the first test's completed
    runs, so it reported their stale scores under the new configs."""
    _migrated_store(workspace)

    ABTestRunner(_config("othello")).run()
    ABTestRunner(_config("othello")).run()

    runs = _ab_runs(workspace)
    assert len(runs) == 4
    assert set(runs.values()) == {("othello", 1)}


@pytest.mark.slow
def test_second_experiment_on_another_scenario_runs_under_that_scenario(workspace: Path) -> None:
    """Fixed arm run ids made an A/B test on a second scenario collide with the first
    scenario's runs, which the runner refuses to continue."""
    _migrated_store(workspace)

    ABTestRunner(_config("othello")).run()
    ABTestRunner(_config("grid_ctf")).run()

    scenarios = sorted(scenario for scenario, _ in _ab_runs(workspace).values())
    assert scenarios == ["grid_ctf", "grid_ctf", "othello", "othello"]


@pytest.mark.slow
def test_runs_against_a_fresh_unmigrated_database(workspace: Path) -> None:
    """The A/B runner never migrated its database, so a fresh workspace crashed
    with 'no such table: runs'."""
    result = ABTestRunner(_config("othello")).run()

    assert len(result.baseline_scores) == len(result.treatment_scores) == 1
    runs = _ab_runs(workspace)
    assert len(runs) == 2
    assert set(runs.values()) == {("othello", 1)}


def test_refuses_an_arm_whose_run_id_already_exists(workspace: Path) -> None:
    """A colliding experiment id must never re-enter existing rows and report them
    as the new experiment's results."""
    store = _migrated_store(workspace)
    for condition in ("baseline", "treatment"):
        store.create_run(f"ab_taken_{condition}_0", "othello", 1, "local")

    with pytest.raises(ValueError, match="ab_taken_(baseline|treatment)_0' already exists"):
        ABTestRunner(_config("othello", experiment_id="taken")).run()

    assert _ab_runs(workspace) == {
        "ab_taken_baseline_0": ("othello", 0),
        "ab_taken_treatment_0": ("othello", 0),
    }


def test_each_config_gets_its_own_experiment_id() -> None:
    """A shared default id would make every A/B test collide with the first one's runs."""
    assert _config("othello").experiment_id != _config("othello").experiment_id
