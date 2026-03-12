from __future__ import annotations

import json
from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner


def test_rlm_enabled_single_generation(tmp_path: Path) -> None:
    """End-to-end: RLM-enabled run with deterministic provider completes successfully."""
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=3000,
        agent_provider="deterministic",
        matches_per_generation=2,
        rlm_enabled=True,
        rlm_max_turns=5,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    run_id = "rlm_test_run"
    summary = runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    assert summary.run_id == run_id
    assert summary.generations_executed == 1

    # Verify artifacts were persisted
    metrics_path = tmp_path / "runs" / run_id / "generations" / "gen_1" / "metrics.json"
    assert metrics_path.exists()
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["generation_index"] == 1
    assert "elo" in payload

    # Verify agent outputs were stored (analyst/architect went through RLM path)
    with runner.sqlite.connect() as conn:
        rows = conn.execute(
            "SELECT role FROM agent_outputs WHERE run_id = ? ORDER BY role",
            (run_id,),
        ).fetchall()
        roles = [row["role"] for row in rows]
        assert "analyst" in roles
        assert "architect" in roles
        assert "competitor" in roles
        assert "coach" in roles

    # Verify agent role metrics show RLM sessions completed
    with runner.sqlite.connect() as conn:
        metrics_rows = conn.execute(
            "SELECT role, status FROM agent_role_metrics WHERE run_id = ? ORDER BY role",
            (run_id,),
        ).fetchall()
        metric_roles = {row["role"] for row in metrics_rows}
        assert "analyst" in metric_roles
        assert "architect" in metric_roles


def test_rlm_two_generations_with_context_accumulation(tmp_path: Path) -> None:
    """RLM context loader picks up artifacts from generation 1 when running generation 2."""
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=4000,
        agent_provider="deterministic",
        matches_per_generation=2,
        rlm_enabled=True,
        rlm_max_turns=5,
        architect_every_n_gens=1,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    run_id = "rlm_multi_gen"
    summary = runner.run(scenario_name="grid_ctf", generations=2, run_id=run_id)
    assert summary.generations_executed == 2

    # Both generations should have metrics
    gen1_metrics = tmp_path / "runs" / run_id / "generations" / "gen_1" / "metrics.json"
    gen2_metrics = tmp_path / "runs" / run_id / "generations" / "gen_2" / "metrics.json"
    assert gen1_metrics.exists()
    assert gen2_metrics.exists()

    # Verify match records accumulated across generations
    matches = runner.sqlite.get_matches_for_run(run_id)
    assert len(matches) == 4  # 2 matches per gen * 2 gens
