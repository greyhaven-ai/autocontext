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


def _mock_operator_loop_result(
    *,
    score: float = 0.8,
    escalations: int = 0,
    clarifications: int = 0,
) -> dict[str, object]:
    return {
        "score": score,
        "reasoning": "Operator loop completed",
        "dimension_scores": {},
        "escalation_count": escalations,
        "clarification_count": clarifications,
    }


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

    def test_tolerates_postconditions_in_llm_generated_actions(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        spec_json = json.dumps({
            "description": "Postconditions simulation",
            "environment_description": "Test env",
            "initial_state_description": "Start state",
            "success_criteria": [{"condition": "complete", "description": "complete all steps"}],
            "failure_modes": [{"condition": "timeout", "description": "run timed out"}],
            "max_steps": 10,
            "actions": [
                {
                    "name": "step_a",
                    "description": "First",
                    "parameters": {},
                    "preconditions": [],
                    "postconditions": ["a_done"],
                    "steps": [{"action": "observe", "condition": "always"}],
                },
                {
                    "name": "step_b",
                    "description": "Second",
                    "parameters": {},
                    "preconditions": ["step_a"],
                    "effects": ["b_done"],
                },
            ],
        })

        engine = SimulationEngine(llm_fn=_mock_llm_fn(spec_json), knowledge_root=tmp_knowledge)
        result = engine.run(description="Simulation with postconditions")

        assert result["status"] == "completed"
        scenario_dir = Path(result["artifacts"]["scenario_dir"])
        persisted = json.loads((scenario_dir / "spec.json").read_text())
        assert persisted["actions"][0]["effects"] == ["a_done"]
        assert persisted["success_criteria"] == ["complete all steps"]
        assert persisted["failure_modes"] == ["run timed out"]

    def test_structured_preconditions_keep_action_dependencies(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        spec_json = json.dumps({
            "description": "Structured precondition simulation",
            "environment_description": "Test env",
            "initial_state_description": "Start state",
            "success_criteria": ["complete all steps"],
            "failure_modes": ["timeout"],
            "max_steps": 10,
            "actions": [
                {
                    "name": "step_a",
                    "description": "First",
                    "parameters": {},
                    "preconditions": [],
                    "effects": ["a_done"],
                },
                {
                    "name": "step_b",
                    "description": "Second",
                    "parameters": {},
                    "preconditions": [{"action": "step_a", "description": "after step a"}],
                    "effects": ["b_done"],
                },
            ],
        })

        engine = SimulationEngine(llm_fn=_mock_llm_fn(spec_json), knowledge_root=tmp_knowledge)
        result = engine.run(description="Simulation with structured preconditions")

        assert result["status"] == "completed"
        assert result["summary"]["reasoning"] == "Completed 2 of 2 required actions."

    def test_operator_loop_run_prefers_safe_autonomy_over_unnecessary_escalation(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        operator_loop_spec = json.dumps({
            "description": "Escalation-first deployment review with ambiguous prerequisites",
            "environment_description": "A deployment requires human confirmation before release.",
            "initial_state_description": "The rollout is blocked until the review is complete.",
            "escalation_policy": {"escalation_threshold": "medium", "max_escalations": 5},
            "success_criteria": ["review completed", "release approved safely"],
            "failure_modes": ["unsafe autonomous release"],
            "max_steps": 5,
            "actions": [
                {
                    "name": "release_to_prod",
                    "description": "Attempt production release",
                    "parameters": {},
                    "preconditions": ["operator_review_complete"],
                    "effects": ["released"],
                },
                {
                    "name": "operator_review_complete",
                    "description": "Record operator review",
                    "parameters": {},
                    "preconditions": [],
                    "effects": ["reviewed"],
                },
            ],
        })

        engine = SimulationEngine(llm_fn=_mock_llm_fn(operator_loop_spec), knowledge_root=tmp_knowledge)
        result = engine.run(description="Simulate an operator escalation for an ambiguous production release")

        assert result["status"] == "completed"
        assert result["family"] == "operator_loop"
        assert result["summary"]["dimension_scores"]["autonomy_efficiency"] == 1.0
        assert "Escalations: 0" in result["summary"]["reasoning"]
        assert "Clarifications: 0" in result["summary"]["reasoning"]
        assert "Missed escalations: 0" in result["summary"]["reasoning"]

    def test_operator_loop_multi_run_preserves_contract_signal_counts(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine._execute_single = lambda source, name, seed, max_steps=None: _mock_operator_loop_result(  # type: ignore[method-assign]
            escalations=1,
            clarifications=1,
        )

        result = engine.run(
            description="Simulate a customer support escalation where the AI agent must escalate to a human operator",
            runs=2,
        )

        assert result["status"] == "completed"
        assert result["summary"]["escalation_count"] == 2
        assert result["summary"]["clarification_count"] == 2

    def test_operator_loop_sweep_preserves_contract_signal_counts(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine._execute_single = lambda source, name, seed, max_steps=None: _mock_operator_loop_result(  # type: ignore[method-assign]
            escalations=1,
            clarifications=0,
        )

        result = engine.run(
            description="Simulate a customer support escalation where the AI agent must escalate to a human operator",
            sweep=[{"name": "seed", "values": [1, 2]}],
        )

        assert result["status"] == "completed"
        assert result["summary"]["escalation_count"] == 2

    def test_clarification_only_prompt_routes_to_operator_loop_contract(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        engine._execute_single = lambda source, name, seed, max_steps=None: _mock_operator_loop_result()  # type: ignore[method-assign]

        result = engine.run(description="Handle requests with incomplete inputs, asking clarifying questions when needed")

        assert result["family"] == "operator_loop"
        assert result["status"] == "completed"
        assert any("clarification" in warning.lower() for warning in result["warnings"])


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

    def test_operator_loop_replay_reapplies_behavioral_contract(self, tmp_knowledge: Path) -> None:
        from autocontext.simulation.engine import SimulationEngine

        engine = SimulationEngine(llm_fn=_mock_llm_fn(), knowledge_root=tmp_knowledge)
        results = iter([
            _mock_operator_loop_result(score=0.9, escalations=1, clarifications=0),
            _mock_operator_loop_result(score=0.8, escalations=0, clarifications=0),
        ])
        engine._execute_single = lambda source, name, seed, max_steps=None: next(results)  # type: ignore[method-assign]

        original = engine.run(
            description="Simulate a customer support escalation where the AI agent must escalate to a human operator",
            save_as="contract_replay",
        )
        replay = engine.replay(id="contract_replay")

        assert original["status"] == "completed"
        assert replay["status"] == "incomplete"
        assert replay["missing_signals"] == ["escalation"]
        assert replay["summary"]["score"] == 0.3


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
