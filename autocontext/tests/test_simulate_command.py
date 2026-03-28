"""AC-453: Python parity for simulate command.

Tests the SimulationEngine that takes plain-language descriptions,
builds simulation specs, executes trajectories/sweeps, and returns
structured findings with assumptions and warnings.
"""

import json
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_knowledge(tmp_path: Path) -> Path:
    return tmp_path / "knowledge"


def _mock_llm_fn(spec_json: str | None = None):
    """Return a callable that mimics llm_fn(system, user) -> str."""
    default = json.dumps({
        "description": "Test simulation",
        "environment_description": "Test env",
        "initial_state_description": "Start state",
        "success_criteria": ["complete all steps"],
        "failure_modes": ["timeout"],
        "max_steps": 10,
        "actions": [
            {"name": "step_a", "description": "First", "parameters": {}, "preconditions": [], "effects": ["a_done"]},
            {"name": "step_b", "description": "Second", "parameters": {}, "preconditions": ["step_a"], "effects": ["b_done"]},
        ],
    })

    def llm_fn(system: str, user: str) -> str:
        return spec_json or default

    return llm_fn


# ---------------------------------------------------------------------------
# SimulationEngine core
# ---------------------------------------------------------------------------


class TestSimulationEngine:
    def test_run_from_description(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        result = engine.run(description="Simulate deploying a web service")

        assert result["status"] == "completed"
        assert result["id"]
        assert result["family"] in ("simulation", "operator_loop")
        assert isinstance(result["assumptions"], list)
        assert len(result["assumptions"]) > 0
        assert isinstance(result["warnings"], list)
        assert any("model" in w.lower() for w in result["warnings"])

    def test_persists_artifacts(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        result = engine.run(description="Artifact test", save_as="art_test")

        scenario_dir = Path(result["artifacts"]["scenario_dir"])
        assert scenario_dir.exists()
        assert (scenario_dir / "spec.json").exists()
        assert (scenario_dir / "scenario.py").exists()

    def test_structured_summary_with_score(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        result = engine.run(description="Score test")

        assert isinstance(result["summary"]["score"], float)
        assert 0 <= result["summary"]["score"] <= 1
        assert isinstance(result["summary"]["reasoning"], str)

    def test_variable_overrides(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        result = engine.run(
            description="Variable test",
            variables={"threshold": 0.8, "budget": 200},
        )

        assert result["variables"]["threshold"] == 0.8
        assert result["variables"]["budget"] == 200

    def test_sweep_execution(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        result = engine.run(
            description="Sweep test",
            sweep=[{"name": "seed", "values": [1, 2, 3]}],
        )

        assert result["status"] == "completed"
        assert result["sweep"] is not None
        assert result["sweep"]["runs"] >= 3

    def test_sweep_best_worst_case(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        result = engine.run(
            description="Best worst test",
            sweep=[{"name": "seed", "values": [1, 2, 3]}],
        )

        assert result["summary"]["best_case"] is not None
        assert result["summary"]["worst_case"] is not None

    def test_sweep_cells_change_execution_when_variables_change_runtime(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        result = engine.run(
            description="Sweep runtime test",
            sweep=[{"name": "max_steps", "values": [1, 2]}],
        )

        assert result["status"] == "completed"
        scores = [row["score"] for row in result["sweep"]["results"]]
        reasons = [row["reasoning"] for row in result["sweep"]["results"]]
        assert len(set(scores)) > 1
        assert len(set(reasons)) > 1


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class TestSimulateReplay:
    def test_replay_saved_simulation(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        original = engine.run(description="Replay test", save_as="replay_test")
        assert original["status"] == "completed"

        replay = engine.replay(id="replay_test")
        assert replay["status"] == "completed"
        assert replay["replay_of"] == "replay_test"
        assert isinstance(replay["original_score"], float)
        assert isinstance(replay["score_delta"], float)

    def test_replay_deterministic(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        original = engine.run(description="Det test", save_as="det_test")
        replay = engine.replay(id="det_test")

        assert replay["summary"]["score"] == original["summary"]["score"]

    def test_replay_nonexistent_fails(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        result = engine.replay(id="nonexistent")
        assert result["status"] == "failed"
        assert "not found" in result["error"]

    def test_replay_override_variables_change_execution(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        original = engine.run(
            description="Replay override test",
            save_as="override_test",
            variables={"max_steps": 10},
        )
        replay = engine.replay(id="override_test", variables={"max_steps": 1})

        assert replay["status"] == "completed"
        assert replay["variables"]["max_steps"] == 1
        assert replay["summary"]["score"] != original["summary"]["score"]
        assert replay["summary"]["reasoning"] != original["summary"]["reasoning"]


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


class TestSimulateCompare:
    def test_compare_two_simulations(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine.run(description="Compare A", save_as="cmp_a")
        engine.run(description="Compare B", save_as="cmp_b")

        result = engine.compare(left="cmp_a", right="cmp_b")
        assert result["status"] == "completed"
        assert isinstance(result["score_delta"], float)
        assert isinstance(result["variable_deltas"], dict)
        assert isinstance(result["likely_drivers"], list)
        assert isinstance(result["summary"], str)

    def test_compare_nonexistent_fails(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine.run(description="Exists", save_as="exists")

        result = engine.compare(left="exists", right="nope")
        assert result["status"] == "failed"
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestSimulateExport:
    def test_export_json(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine
        from autocontext.simulation.export import export_simulation

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine.run(description="Export test", save_as="exp_test")

        result = export_simulation(id="exp_test", knowledge_root=tmp_knowledge, format="json")
        assert result["status"] == "completed"
        assert Path(result["output_path"]).exists()

        pkg = json.loads(Path(result["output_path"]).read_text())
        assert pkg["name"] == "exp_test"
        assert "assumptions" in pkg
        assert "warnings" in pkg

    def test_export_markdown(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine
        from autocontext.simulation.export import export_simulation

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine.run(description="MD test", save_as="md_test")

        result = export_simulation(id="md_test", knowledge_root=tmp_knowledge, format="markdown")
        assert result["status"] == "completed"
        content = Path(result["output_path"]).read_text()
        assert "# Simulation Report" in content
        assert "Assumptions" in content

    def test_export_replay_id(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine
        from autocontext.simulation.export import export_simulation

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine.run(description="Replay export test", save_as="replay_export")
        replay = engine.replay(id="replay_export")

        result = export_simulation(id=replay["id"], knowledge_root=tmp_knowledge, format="json")
        assert result["status"] == "completed"
        pkg = json.loads(Path(result["output_path"]).read_text())
        assert pkg["id"] == replay["id"]
        assert pkg["replay_of"] == "replay_export"

    def test_export_csv(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine
        from autocontext.simulation.export import export_simulation

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine.run(
            description="CSV export test",
            save_as="csv_test",
            sweep=[{"name": "max_steps", "values": [1, 2]}],
        )

        result = export_simulation(id="csv_test", knowledge_root=tmp_knowledge, format="csv")
        assert result["status"] == "completed"
        assert result["format"] == "csv"
        content = Path(result["output_path"]).read_text()
        header = content.splitlines()[0]
        assert "max_steps" in header
        assert "score" in header

    def test_export_invalid_format_fails_cleanly(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine
        from autocontext.simulation.export import export_simulation

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine.run(description="Bad format test", save_as="bad_fmt")

        result = export_simulation(id="bad_fmt", knowledge_root=tmp_knowledge, format="yaml")
        assert result["status"] == "failed"
        assert "Unsupported export format" in result["error"]
