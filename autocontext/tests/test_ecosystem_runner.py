"""Tests for ecosystem loop runner — provider-alternating multi-cycle runs."""
from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner
from autocontext.storage import SQLiteStore


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "migrations"


def _make_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    defaults: dict[str, object] = dict(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
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


# ---------- Phase 1: Migration 005 ----------


def test_migration_005_adds_agent_provider_to_runs(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.migrate(_migrations_dir())
    with store.connect() as conn:
        # Should be able to read agent_provider column
        conn.execute(
            "INSERT INTO runs(run_id, scenario, target_generations, executor_mode, status, agent_provider) "
            "VALUES ('mig_test', 'grid_ctf', 1, 'local', 'running', 'anthropic')"
        )
        row = conn.execute("SELECT agent_provider FROM runs WHERE run_id = 'mig_test'").fetchone()
        assert row is not None
        assert row["agent_provider"] == "anthropic"


def test_migration_005_adds_provider_to_knowledge_snapshots(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.migrate(_migrations_dir())
    with store.connect() as conn:
        # Create a run to satisfy FK
        conn.execute(
            "INSERT INTO runs(run_id, scenario, target_generations, executor_mode, status) "
            "VALUES ('snap_mig_test', 'grid_ctf', 1, 'local', 'running')"
        )
        conn.execute(
            "INSERT INTO knowledge_snapshots(scenario, run_id, best_score, best_elo, playbook_hash, agent_provider, rlm_enabled) "
            "VALUES ('grid_ctf', 'snap_mig_test', 0.5, 1000.0, 'hash1', 'agent_sdk', 1)"
        )
        row = conn.execute(
            "SELECT agent_provider, rlm_enabled FROM knowledge_snapshots WHERE run_id = 'snap_mig_test'"
        ).fetchone()
        assert row is not None
        assert row["agent_provider"] == "agent_sdk"
        assert row["rlm_enabled"] == 1


def test_migration_005_defaults_for_existing_rows(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.migrate(_migrations_dir())
    with store.connect() as conn:
        # Insert a run without specifying agent_provider — should default to ''
        conn.execute(
            "INSERT INTO runs(run_id, scenario, target_generations, executor_mode, status) "
            "VALUES ('default_test', 'grid_ctf', 1, 'local', 'running')"
        )
        row = conn.execute("SELECT agent_provider FROM runs WHERE run_id = 'default_test'").fetchone()
        assert row is not None
        assert row["agent_provider"] == ""


# ---------- Phase 2: SQLiteStore changes ----------


def test_create_run_stores_agent_provider(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.migrate(_migrations_dir())
    store.create_run("prov_run", "grid_ctf", 1, "local", agent_provider="anthropic")
    with store.connect() as conn:
        row = conn.execute("SELECT agent_provider FROM runs WHERE run_id = 'prov_run'").fetchone()
        assert row is not None
        assert row["agent_provider"] == "anthropic"


def test_create_run_default_agent_provider(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.migrate(_migrations_dir())
    store.create_run("default_prov_run", "grid_ctf", 1, "local")
    with store.connect() as conn:
        row = conn.execute("SELECT agent_provider FROM runs WHERE run_id = 'default_prov_run'").fetchone()
        assert row is not None
        assert row["agent_provider"] == ""


def test_save_knowledge_snapshot_with_provider(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.migrate(_migrations_dir())
    store.create_run("snap_prov_run", "grid_ctf", 1, "local")
    store.save_knowledge_snapshot(
        "grid_ctf", "snap_prov_run", 0.7, 1050.0, "hash_prov",
        agent_provider="agent_sdk", rlm_enabled=True,
    )
    with store.connect() as conn:
        row = conn.execute(
            "SELECT agent_provider, rlm_enabled FROM knowledge_snapshots WHERE run_id = 'snap_prov_run'"
        ).fetchone()
        assert row is not None
        assert row["agent_provider"] == "agent_sdk"
        assert row["rlm_enabled"] == 1


def test_get_ecosystem_snapshots(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    store.migrate(_migrations_dir())
    store.create_run("eco_r1", "grid_ctf", 1, "local")
    store.create_run("eco_r2", "grid_ctf", 1, "local")
    store.create_run("eco_r3", "othello", 1, "local")
    store.save_knowledge_snapshot("grid_ctf", "eco_r1", 0.5, 1000.0, "h1", agent_provider="anthropic", rlm_enabled=True)
    store.save_knowledge_snapshot("grid_ctf", "eco_r2", 0.8, 1100.0, "h2", agent_provider="agent_sdk")
    store.save_knowledge_snapshot("othello", "eco_r3", 0.3, 900.0, "h3", agent_provider="deterministic")
    snapshots = store.get_ecosystem_snapshots("grid_ctf")
    assert len(snapshots) == 2
    # Ordered by created_at ASC
    assert snapshots[0]["run_id"] == "eco_r1"
    assert snapshots[0]["agent_provider"] == "anthropic"
    assert snapshots[0]["rlm_enabled"] == 1
    assert snapshots[1]["run_id"] == "eco_r2"
    assert snapshots[1]["agent_provider"] == "agent_sdk"
    assert snapshots[1]["rlm_enabled"] == 0


# ---------- Phase 2b: GenerationRunner wiring ----------


def test_generation_runner_stores_provider_in_run(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, agent_provider="deterministic")
    runner = GenerationRunner(settings)
    runner.migrate(_migrations_dir())
    runner.run(scenario_name="grid_ctf", generations=1, run_id="wired_run")
    with runner.sqlite.connect() as conn:
        row = conn.execute("SELECT agent_provider FROM runs WHERE run_id = 'wired_run'").fetchone()
        assert row is not None
        assert row["agent_provider"] == "deterministic"


def test_generation_runner_stores_provider_in_snapshot(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, agent_provider="deterministic", rlm_enabled=False)
    runner = GenerationRunner(settings)
    runner.migrate(_migrations_dir())
    runner.run(scenario_name="grid_ctf", generations=1, run_id="snap_wired_run")
    with runner.sqlite.connect() as conn:
        row = conn.execute(
            "SELECT agent_provider, rlm_enabled FROM knowledge_snapshots WHERE run_id = 'snap_wired_run'"
        ).fetchone()
        assert row is not None
        assert row["agent_provider"] == "deterministic"
        assert row["rlm_enabled"] == 0


# ---------- Phase 3: EcosystemRunner ----------


def test_ecosystem_phase_dataclass() -> None:
    from autocontext.loop.ecosystem_runner import EcosystemPhase

    phase = EcosystemPhase(provider="anthropic", rlm_enabled=True, generations=3)
    assert phase.provider == "anthropic"
    assert phase.rlm_enabled is True
    assert phase.generations == 3


def test_ecosystem_config_default_phases() -> None:
    from autocontext.loop.ecosystem_runner import EcosystemConfig

    config = EcosystemConfig(scenario="grid_ctf", cycles=3, gens_per_cycle=2)
    assert len(config.phases) == 2
    assert config.phases[0].provider == "anthropic"
    assert config.phases[0].rlm_enabled is True
    assert config.phases[1].provider == "agent_sdk"
    assert config.phases[1].rlm_enabled is False


def test_ecosystem_config_custom_phases() -> None:
    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase

    custom = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=True, generations=2),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=3),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=2, gens_per_cycle=1, phases=custom)
    assert len(config.phases) == 3
    assert config.phases[2].generations == 3


def test_ecosystem_run_id_pattern() -> None:
    from autocontext.loop.ecosystem_runner import EcosystemRunner

    settings = _make_settings(Path("/tmp/unused"))
    from autocontext.loop.ecosystem_runner import EcosystemConfig

    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=1)
    runner = EcosystemRunner(settings, config)
    rid = runner._make_run_id("grid_ctf", 1, 0)
    assert rid.startswith("eco_grid_ctf_c1_p0_")
    assert len(rid) > len("eco_grid_ctf_c1_p0_")


def test_ecosystem_runner_creates_modified_settings(tmp_path: Path) -> None:
    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    base = _make_settings(tmp_path)
    phase = EcosystemPhase(provider="agent_sdk", rlm_enabled=True, generations=2)
    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=2)
    runner = EcosystemRunner(base, config)
    modified = runner._phase_settings(phase)
    assert modified.agent_provider == "agent_sdk"
    assert modified.rlm_enabled is True
    # Storage roots should be preserved
    assert modified.db_path == base.db_path
    assert modified.knowledge_root == base.knowledge_root
    assert modified.runs_root == base.runs_root


def test_ecosystem_single_cycle_deterministic(tmp_path: Path) -> None:
    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    summary = runner.run()
    assert len(summary.run_summaries) == 2  # 1 cycle * 2 phases
    assert summary.scenario == "grid_ctf"
    assert summary.cycles == 1


def test_ecosystem_multi_cycle_deterministic(tmp_path: Path) -> None:
    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=2, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    summary = runner.run()
    assert len(summary.run_summaries) == 4  # 2 cycles * 2 phases
    assert summary.cycles == 2


def test_ecosystem_phases_share_knowledge_directory(tmp_path: Path) -> None:
    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    runner.run()
    # Both phases should write to the same knowledge directory
    playbook = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook.exists()


def test_ecosystem_provider_tracked_in_db(tmp_path: Path) -> None:
    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    summary = runner.run()
    store = SQLiteStore(base.db_path)
    for rs in summary.run_summaries:
        with store.connect() as conn:
            row = conn.execute("SELECT agent_provider FROM runs WHERE run_id = ?", (rs.run_id,)).fetchone()
            assert row is not None
            assert row["agent_provider"] == "deterministic"


def test_ecosystem_emits_lifecycle_events(tmp_path: Path) -> None:
    import json

    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    runner.run()
    events_path = tmp_path / "runs" / "events.ndjson"
    assert events_path.exists()
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").strip().split("\n")]
    eco_events = [e for e in events if e.get("channel") == "ecosystem"]
    event_types = [e["event"] for e in eco_events]
    assert "ecosystem_started" in event_types
    assert "ecosystem_cycle_started" in event_types
    assert "ecosystem_cycle_completed" in event_types
    assert "ecosystem_completed" in event_types


def test_ecosystem_summary_has_score_trajectory(tmp_path: Path) -> None:
    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    base = _make_settings(tmp_path)
    phases = [
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
        EcosystemPhase(provider="deterministic", rlm_enabled=False, generations=1),
    ]
    config = EcosystemConfig(scenario="grid_ctf", cycles=1, gens_per_cycle=1, phases=phases)
    runner = EcosystemRunner(base, config)
    runner.migrate(_migrations_dir())
    summary = runner.run()
    trajectory = summary.score_trajectory()
    assert len(trajectory) == 2
    for run_id, score in trajectory:
        assert isinstance(run_id, str)
        assert isinstance(score, float)
