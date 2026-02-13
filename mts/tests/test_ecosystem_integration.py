"""End-to-end integration tests for ecosystem loop."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mts.config import AppSettings
from mts.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner
from mts.storage import SQLiteStore


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "migrations"


def _make_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    defaults: dict[str, object] = dict(
        db_path=tmp_path / "runs" / "mts.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        cross_run_inheritance=True,
    )
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


def test_full_ecosystem_two_cycles(tmp_path: Path) -> None:
    """2 cycles * 2 phases = 4 runs, verify DB + filesystem."""
    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=2, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    summary = runner.run()

    assert len(summary.run_summaries) == 4
    assert summary.scenario == "grid_ctf"
    assert summary.cycles == 2

    # All 4 runs should be in DB
    store = SQLiteStore(base.db_path)
    with store.connect() as conn:
        rows = conn.execute("SELECT run_id FROM runs WHERE run_id LIKE 'eco_%'").fetchall()
    assert len(rows) == 4

    # Knowledge directory should have playbook
    playbook = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook.exists()


def test_ecosystem_with_rlm_phase(tmp_path: Path) -> None:
    """One RLM-enabled + one non-RLM phase, both complete."""
    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=True, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    summary = runner.run()

    assert len(summary.run_summaries) == 2
    # Both should complete
    for rs in summary.run_summaries:
        assert rs.generations_executed == 1


def test_ecosystem_knowledge_snapshots_have_provider(tmp_path: Path) -> None:
    """Provider metadata is recorded in knowledge snapshots."""
    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    runner.run()

    store = SQLiteStore(base.db_path)
    snapshots = store.get_ecosystem_snapshots("grid_ctf")
    assert len(snapshots) == 2
    for snap in snapshots:
        assert snap["agent_provider"] == "deterministic"


def test_ecosystem_score_trajectory(tmp_path: Path) -> None:
    """Trajectory data available for analysis."""
    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=2, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    summary = runner.run()

    trajectory = summary.score_trajectory()
    assert len(trajectory) == 2
    for run_id, score in trajectory:
        assert run_id.startswith("eco_grid_ctf_")
        assert isinstance(score, float)


def test_ecosystem_othello_scenario(tmp_path: Path) -> None:
    """Works with alternate scenario."""
    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="othello", cycles=1, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    summary = runner.run()

    assert len(summary.run_summaries) == 1
    assert summary.scenario == "othello"
    assert summary.run_summaries[0].scenario == "othello"


def test_ecosystem_cli_command_exists() -> None:
    """The ecosystem CLI command is registered."""
    from mts.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["ecosystem", "--help"])
    assert result.exit_code == 0
    assert "ecosystem" in result.output.lower()
    assert "--cycles" in result.output
    assert "--provider-a" in result.output
    assert "--provider-b" in result.output


def test_ecosystem_cli_runs_deterministic(tmp_path: Path, monkeypatch: object) -> None:
    """CLI command runs end-to-end with deterministic provider."""
    from mts.cli import app

    # monkeypatch env vars to point at tmp_path
    mp = monkeypatch  # type: ignore[assignment]
    mp.setenv("MTS_AGENT_PROVIDER", "deterministic")
    mp.setenv("MTS_DB_PATH", str(tmp_path / "runs" / "mts.sqlite3"))
    mp.setenv("MTS_RUNS_ROOT", str(tmp_path / "runs"))
    mp.setenv("MTS_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    mp.setenv("MTS_SKILLS_ROOT", str(tmp_path / "skills"))
    mp.setenv("MTS_EVENT_STREAM_PATH", str(tmp_path / "runs" / "events.ndjson"))
    mp.setenv("MTS_MATCHES_PER_GENERATION", "2")

    runner = CliRunner()
    result = runner.invoke(app, [
        "ecosystem",
        "--scenario", "grid_ctf",
        "--cycles", "1",
        "--gens-per-cycle", "1",
        "--provider-a", "deterministic",
        "--provider-b", "deterministic",
        "--no-rlm-a",
        "--no-rlm-b",
    ])
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Ecosystem Summary" in result.output
    assert "Score Trajectory" in result.output
