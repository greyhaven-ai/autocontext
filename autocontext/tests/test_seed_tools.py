"""Tests for Gap 3: Wire seed_tools() hook for scenarios."""
from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner
from autocontext.storage.artifacts import ArtifactStore


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


def test_seed_tools_called_before_gen_1(tmp_path: Path, monkeypatch: object) -> None:
    """Scenario's seed_tools invoked when tools dir is empty."""
    import autocontext.scenarios.grid_ctf.scenario as grid_mod

    seed_called = []

    def mock_seed_tools(self: object) -> dict[str, str]:
        seed_called.append(True)
        return {"helper": "def helper(): return 42"}

    monkeypatch.setattr(grid_mod.GridCtfScenario, "seed_tools", mock_seed_tools)  # type: ignore[arg-type]

    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    runner.run(scenario_name="grid_ctf", generations=1, run_id="seed_test")
    assert len(seed_called) > 0, "seed_tools() should be called before first generation"
    assert (tmp_path / "knowledge" / "grid_ctf" / "tools" / "helper.py").exists()


def test_seed_tools_not_called_when_tools_exist(tmp_path: Path, monkeypatch: object) -> None:
    """Existing tools dir skips seed_tools call."""
    import autocontext.scenarios.grid_ctf.scenario as grid_mod

    seed_called = []

    def mock_seed_tools(self: object) -> dict[str, str]:
        seed_called.append(True)
        return {"helper": "def helper(): return 42"}

    monkeypatch.setattr(grid_mod.GridCtfScenario, "seed_tools", mock_seed_tools)  # type: ignore[arg-type]

    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
    )
    # Pre-create the tools directory with a file
    tool_dir = tmp_path / "knowledge" / "grid_ctf" / "tools"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "existing.py").write_text("x = 1\n", encoding="utf-8")

    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    runner.run(scenario_name="grid_ctf", generations=1, run_id="seed_skip")
    assert len(seed_called) == 0, "seed_tools() should NOT be called when tools dir already exists"


def test_seed_tools_persisted_via_persist_tools(tmp_path: Path) -> None:
    """Seed tools are written through persist_tools (which validates syntax)."""
    store = _make_store(tmp_path)
    # Simulate what generation_runner would do
    seed = {"calc": "def calc(x): return x * 2"}
    seed_tool_list = [{"name": k, "code": v, "description": f"Seed tool: {k}"} for k, v in seed.items()]
    created = store.persist_tools("grid_ctf", 0, seed_tool_list)
    assert "calc.py" in created
    content = (store.tools_dir("grid_ctf") / "calc.py").read_text(encoding="utf-8")
    assert "generation 0" in content


def test_seed_tools_empty_dict_no_op(tmp_path: Path) -> None:
    """Empty {} from seed_tools creates no files."""
    store = _make_store(tmp_path)
    seed: dict[str, str] = {}
    seed_tool_list = [{"name": k, "code": v, "description": f"Seed tool: {k}"} for k, v in seed.items()]
    created = store.persist_tools("grid_ctf", 0, seed_tool_list)
    assert created == []
    assert not store.tools_dir("grid_ctf").exists()
