"""Tests for Score Trajectory + Strategy Registry (Batch 1)."""
from __future__ import annotations

from pathlib import Path

from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder
from autocontext.prompts.templates import build_prompt_bundle
from autocontext.scenarios.base import Observation
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "autocontext.sqlite3")
    store.migrate(MIGRATIONS_DIR)
    return store


def _insert_generation(
    store: SQLiteStore,
    run_id: str,
    gen: int,
    mean: float,
    best: float,
    elo: float,
    gate: str,
) -> None:
    store.upsert_generation(
        run_id, gen,
        mean_score=mean, best_score=best, elo=elo,
        wins=1, losses=0, gate_decision=gate, status="completed",
    )


# --- ScoreTrajectoryBuilder.build_trajectory tests ---

def test_trajectory_empty_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("empty_run", "grid_ctf", 3, "local")
    builder = ScoreTrajectoryBuilder(store)
    assert builder.build_trajectory("empty_run") == ""


def test_trajectory_single_gen(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("run1", "grid_ctf", 1, "local")
    _insert_generation(store, "run1", 1, mean=0.45, best=0.50, elo=1010.0, gate="advance")
    builder = ScoreTrajectoryBuilder(store)
    result = builder.build_trajectory("run1")
    assert "## Score Trajectory" in result
    lines = result.strip().split("\n")
    # Header (2 lines) + separator + 1 data row = 4 lines total
    assert len(lines) == 4 + 1  # header, blank, col headers, separator, 1 row
    assert "| 1 " in lines[-1]
    assert "0.4500" in lines[-1]
    assert "+0.5000" in lines[-1]


def test_trajectory_multi_gen(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("run2", "grid_ctf", 3, "local")
    _insert_generation(store, "run2", 1, mean=0.40, best=0.50, elo=1010.0, gate="advance")
    _insert_generation(store, "run2", 2, mean=0.55, best=0.60, elo=1020.0, gate="advance")
    _insert_generation(store, "run2", 3, mean=0.58, best=0.60, elo=1020.0, gate="rollback")
    builder = ScoreTrajectoryBuilder(store)
    result = builder.build_trajectory("run2")
    data_lines = [line for line in result.strip().split("\n") if line.startswith("| ") and not line.startswith("|--")]
    # Skip header row
    data_lines = [line for line in data_lines if not line.startswith("| Gen")]
    assert len(data_lines) == 3
    # First gen delta = best - 0.0 = 0.5
    assert "+0.5000" in data_lines[0]
    # Second gen delta = 0.6 - 0.5 = 0.1
    assert "+0.1000" in data_lines[1]
    # Third gen delta = 0.6 - 0.6 = 0.0
    assert "+0.0000" in data_lines[2]


def test_trajectory_includes_gate(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("gate_run", "grid_ctf", 2, "local")
    _insert_generation(store, "gate_run", 1, mean=0.40, best=0.50, elo=1010.0, gate="advance")
    _insert_generation(store, "gate_run", 2, mean=0.35, best=0.40, elo=1005.0, gate="rollback")
    builder = ScoreTrajectoryBuilder(store)
    result = builder.build_trajectory("gate_run")
    assert "advance" in result
    assert "rollback" in result


# --- ScoreTrajectoryBuilder.build_strategy_registry tests ---

def test_registry_empty(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("empty_run", "grid_ctf", 3, "local")
    builder = ScoreTrajectoryBuilder(store)
    assert builder.build_strategy_registry("empty_run") == ""


def test_registry_maps_strategy_to_score(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("reg_run", "grid_ctf", 1, "local")
    _insert_generation(store, "reg_run", 1, mean=0.45, best=0.50, elo=1010.0, gate="advance")
    store.append_agent_output("reg_run", 1, "competitor", '{"aggression": 0.7}')
    builder = ScoreTrajectoryBuilder(store)
    result = builder.build_strategy_registry("reg_run")
    assert "## Strategy-Score Registry" in result
    assert "aggression" in result
    assert "0.5000" in result
    assert "advance" in result


def test_registry_truncates_long_strategies(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("trunc_run", "grid_ctf", 1, "local")
    _insert_generation(store, "trunc_run", 1, mean=0.45, best=0.50, elo=1010.0, gate="advance")
    long_strategy = '{"key": "' + "x" * 250 + '"}'
    store.append_agent_output("trunc_run", 1, "competitor", long_strategy)
    builder = ScoreTrajectoryBuilder(store)
    result = builder.build_strategy_registry("trunc_run")
    assert "..." in result
    # The full long_strategy should NOT appear
    assert long_strategy not in result


# --- Prompt injection tests ---

def test_trajectory_in_competitor_prompt(tmp_path: Path) -> None:
    trajectory_md = "## Score Trajectory\n\n| Gen | Mean |\n|-----|------|\n| 1 | 0.50 |"
    prompts = build_prompt_bundle(
        scenario_rules="Test rules",
        strategy_interface='{"aggression": float}',
        evaluation_criteria="Win rate",
        previous_summary="best score: 0.5",
        observation=Observation(narrative="Test", state={}, constraints=[]),
        current_playbook="No playbook yet.",
        available_tools="No tools.",
        score_trajectory=trajectory_md,
    )
    assert "Score trajectory" in prompts.competitor
    assert "## Score Trajectory" in prompts.competitor


def test_trajectory_in_analyst_prompt(tmp_path: Path) -> None:
    trajectory_md = "## Score Trajectory\n\n| Gen | Mean |\n|-----|------|\n| 1 | 0.50 |"
    prompts = build_prompt_bundle(
        scenario_rules="Test rules",
        strategy_interface='{"aggression": float}',
        evaluation_criteria="Win rate",
        previous_summary="best score: 0.5",
        observation=Observation(narrative="Test", state={}, constraints=[]),
        current_playbook="No playbook yet.",
        available_tools="No tools.",
        score_trajectory=trajectory_md,
    )
    assert "Score trajectory" in prompts.analyst
    assert "## Score Trajectory" in prompts.analyst


def test_trajectory_absent_when_empty(tmp_path: Path) -> None:
    prompts = build_prompt_bundle(
        scenario_rules="Test rules",
        strategy_interface='{"aggression": float}',
        evaluation_criteria="Win rate",
        previous_summary="best score: 0.0",
        observation=Observation(narrative="Test", state={}, constraints=[]),
        current_playbook="No playbook yet.",
        available_tools="No tools.",
        score_trajectory="",
        strategy_registry="",
    )
    assert "Score trajectory" not in prompts.competitor
    assert "Strategy-score registry" not in prompts.competitor
