"""AC-563 Failure A: custom scenario registry isolation.

One malformed ``spec.json`` must not prevent the registry from loading other
scenarios, and must not dump a traceback into stderr for unrelated commands.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from autocontext.scenarios.custom.registry import (
    ScenarioLoadError,
    ScenarioRegistryLoadResult,
    _reconstruct_family_spec,
    load_all_custom_scenarios,
    load_custom_scenarios_detailed,
)
from autocontext.security.child_process_env import child_process_env_without_control_plane_secrets


def test_importing_scenarios_never_loads_persisted_project_code() -> None:
    script = """
import sys
import types

custom = types.ModuleType("autocontext.scenarios.custom")
custom.__path__ = []
registry = types.ModuleType("autocontext.scenarios.custom.registry")

def fail_if_loaded(_root):
    raise RuntimeError("persisted scenario loader ran during import")

registry.load_all_custom_scenarios = fail_if_loaded
sys.modules["autocontext.scenarios.custom"] = custom
sys.modules["autocontext.scenarios.custom.registry"] = registry
import autocontext.scenarios
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        env=child_process_env_without_control_plane_secrets(),
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def _write_valid_parametric_spec(knowledge_root: Path, name: str = "good_scenario") -> Path:
    """Write a minimally-valid parametric ``spec.json`` that the loader can materialize."""
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "spec.json").write_text(
        json.dumps(
            {
                "name": name,
                "display_name": "Good Scenario",
                "description": "Valid parametric scenario used by AC-563 isolation tests.",
                "strategy_interface_description": ("Return JSON with a single float `bias` in [0,1]."),
                "evaluation_criteria": "Reward a bias close to 0.5.",
                "strategy_params": [
                    {
                        "name": "bias",
                        "description": "Decision bias.",
                        "min_value": 0.0,
                        "max_value": 1.0,
                        "default": 0.5,
                    }
                ],
                "environment_variables": [
                    {
                        "name": "noise",
                        "description": "Environmental noise.",
                        "low": 0.0,
                        "high": 0.1,
                    }
                ],
                "scoring_components": [
                    {
                        "name": "centered",
                        "description": "Reward centered bias.",
                        "formula_terms": {"bias": 1.0},
                        "noise_range": [0.0, 0.0],
                    }
                ],
                "final_score_weights": {"centered": 1.0},
                "win_threshold": 0.5,
                "scenario_type": "parametric",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return scenario_dir


def _write_unknown_marker_scenario(knowledge_root: Path, name: str = "banana_scenario") -> Path:
    """Write a scenario dir that claims an unknown family marker."""
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario_type.txt").write_text("banana", encoding="utf-8")
    (scenario_dir / "spec.json").write_text(json.dumps({"name": name, "scenario_type": "banana"}), encoding="utf-8")
    return scenario_dir


def _write_spec_only_agent_task(knowledge_root: Path, name: str = "spec_only_task") -> Path:
    """Write a scenario dir with spec.json and agent_task marker but no agent_task.py."""
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario_type.txt").write_text("agent_task", encoding="utf-8")
    (scenario_dir / "spec.json").write_text(
        json.dumps(
            {
                "name": name,
                "display_name": "Spec Only Task",
                "description": "Has spec but no compiled source.",
                "strategy_interface_description": "ignored",
                "evaluation_criteria": "ignored",
                "scenario_type": "agent_task",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return scenario_dir


def _write_agent_task_with_import_file_not_found(
    knowledge_root: Path,
    name: str = "broken_import_task",
) -> Path:
    """Write an agent_task source that raises FileNotFoundError while importing."""
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario_type.txt").write_text("agent_task", encoding="utf-8")
    (scenario_dir / "spec.json").write_text(json.dumps({"name": name, "scenario_type": "agent_task"}), encoding="utf-8")
    (scenario_dir / "agent_task.py").write_text(
        "from pathlib import Path\nPath(__file__).with_name('missing-data.txt').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    return scenario_dir


def _write_ts_simulation_spec(knowledge_root: Path, name: str = "ts_simulation") -> Path:
    """Write a scenario dir that mimics TS new-scenario output for a simulation family."""
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario_type.txt").write_text("simulation", encoding="utf-8")
    (scenario_dir / "scenario.js").write_text("// TS generated source", encoding="utf-8")
    (scenario_dir / "spec.json").write_text(
        json.dumps(
            {
                "name": name,
                "scenario_type": "simulation",
                "family": "simulation",
                "description": "A test simulation created by TS.",
                "environment_description": "A simulated environment with two variables.",
                "initial_state_description": "Both variables start at zero.",
                "success_criteria": ["Variable A reaches 10"],
                "failure_modes": ["Variable A goes negative"],
                "actions": [
                    {
                        "name": "increment_a",
                        "description": "Add 1 to variable A",
                        "parameters": {},
                        "preconditions": ["Variable A is below 10"],
                        "effects": ["Variable A increases by 1"],
                    },
                    {
                        "name": "reset_a",
                        "description": "Reset variable A to zero",
                        "parameters": {},
                        "preconditions": [],
                        "effects": ["Variable A becomes 0"],
                    },
                ],
                "max_steps": 5,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return scenario_dir


def _write_ts_investigation_spec(knowledge_root: Path, name: str = "ts_investigation") -> Path:
    """Write a scenario dir that mimics TS new-scenario output for investigation family."""
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario_type.txt").write_text("investigation", encoding="utf-8")
    (scenario_dir / "scenario.js").write_text("// TS generated source", encoding="utf-8")
    (scenario_dir / "spec.json").write_text(
        json.dumps(
            {
                "name": name,
                "scenario_type": "investigation",
                "family": "investigation",
                "description": "A test investigation created by TS.",
                "environment_description": "A system with intermittent failures.",
                "initial_state_description": "System is in degraded state.",
                "evidence_pool_description": "Logs, metrics, and traces are available.",
                "diagnosis_target": "Identify the root cause of the degraded state.",
                "success_criteria": ["Root cause correctly identified"],
                "failure_modes": ["Wrong diagnosis accepted"],
                "actions": [
                    {
                        "name": "check_logs",
                        "description": "Review system logs",
                        "parameters": {},
                        "preconditions": [],
                        "effects": ["Log entries revealed"],
                    },
                ],
                "max_steps": 5,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return scenario_dir


def _write_malformed_spec(knowledge_root: Path, name: str = "regression_probe") -> Path:
    """Write a ``spec.json`` that is missing a required pydantic field."""
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "spec.json").write_text(
        json.dumps(
            {
                # Intentionally missing `evaluation_criteria` (required on ScenarioSpec).
                "name": name,
                "display_name": "Regression probe",
                "description": "Intentionally invalid fixture.",
                "strategy_interface_description": "ignored",
                "scenario_type": "parametric",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return scenario_dir


class TestRegistryIsolation:
    def test_malformed_spec_does_not_prevent_other_scenarios_from_loading(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_valid_parametric_spec(knowledge_root, name="good_scenario")
        _write_malformed_spec(knowledge_root, name="regression_probe")

        loaded = load_all_custom_scenarios(knowledge_root)

        assert "good_scenario" in loaded
        assert "regression_probe" not in loaded

    def test_warning_logged_at_warning_level_without_traceback(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_malformed_spec(knowledge_root, name="regression_probe")

        with caplog.at_level(logging.WARNING, logger="autocontext.scenarios.custom.registry"):
            load_all_custom_scenarios(knowledge_root)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"expected exactly one warning, got {[r.message for r in warnings]}"
        record = warnings[0]

        assert record.exc_text is None, "warning must not carry a traceback"
        message = record.getMessage()
        assert "\n" not in message, f"warning must be a single line, got:\n{message!r}"
        assert "regression_probe" in message
        assert "spec.json" in message

    def test_reason_summarizes_pydantic_validation_error(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_malformed_spec(knowledge_root, name="regression_probe")

        with caplog.at_level(logging.WARNING, logger="autocontext.scenarios.custom.registry"):
            load_all_custom_scenarios(knowledge_root)

        message = caplog.records[0].getMessage()
        assert "evaluation_criteria" in message, message
        assert "Traceback" not in message, message
        assert 'File "' not in message, message

    def test_traceback_available_at_debug_level(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_malformed_spec(knowledge_root, name="regression_probe")

        with caplog.at_level(logging.DEBUG, logger="autocontext.scenarios.custom.registry"):
            load_all_custom_scenarios(knowledge_root)

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any(r.exc_text for r in debug_records), "at DEBUG level the full traceback must be available via exc_info"

    def test_reason_identifies_unknown_marker(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_unknown_marker_scenario(knowledge_root, name="banana_scenario")

        with caplog.at_level(logging.WARNING, logger="autocontext.scenarios.custom.registry"):
            load_all_custom_scenarios(knowledge_root)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "banana_scenario" in message
        assert "unknown scenario_type marker" in message
        assert "banana" in message

    def test_malformed_spec_is_reported_in_detailed_result(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_valid_parametric_spec(knowledge_root, name="good_scenario")
        _write_malformed_spec(knowledge_root, name="regression_probe")

        result = load_custom_scenarios_detailed(knowledge_root)

        assert isinstance(result, ScenarioRegistryLoadResult)
        assert "good_scenario" in result.loaded
        assert "regression_probe" not in result.loaded
        assert len(result.skipped) == 1
        entry = result.skipped[0]
        assert isinstance(entry, ScenarioLoadError)
        assert entry.name == "regression_probe"
        assert entry.spec_path == (knowledge_root / "_custom_scenarios" / "regression_probe" / "spec.json")
        assert "evaluation_criteria" in entry.reason
        assert entry.marker == "parametric"

    def test_skipped_tuple_is_immutable(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_malformed_spec(knowledge_root)

        result = load_custom_scenarios_detailed(knowledge_root)

        with pytest.raises((TypeError, AttributeError)):
            result.skipped.append(  # type: ignore[attr-defined]
                ScenarioLoadError(name="x", spec_path=Path("x"), reason="x", marker="x")
            )

    def test_empty_knowledge_root_returns_empty_result(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"  # does not exist

        with caplog.at_level(logging.WARNING, logger="autocontext.scenarios.custom.registry"):
            result = load_custom_scenarios_detailed(knowledge_root)

        assert result.loaded == {}
        assert result.skipped == ()
        assert caplog.records == []

    def test_file_not_found_is_not_reported_as_skipped(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Failure B boundary: spec-less scenario dirs remain silently skipped.

        A scenario directory that declares marker 'agent_task' but has no
        agent_task.py raises FileNotFoundError. That pathway is handled by
        Failure B — preserve today's silent behavior here to avoid double-
        counting.
        """
        knowledge_root = tmp_path / "knowledge"
        scenario_dir = knowledge_root / "_custom_scenarios" / "spec_only_task"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "scenario_type.txt").write_text("agent_task", encoding="utf-8")
        # Deliberately no agent_task.py and no spec.json

        with caplog.at_level(logging.WARNING, logger="autocontext.scenarios.custom.registry"):
            result = load_custom_scenarios_detailed(knowledge_root)

        assert "spec_only_task" not in result.loaded
        assert all(e.name != "spec_only_task" for e in result.skipped)
        assert caplog.records == []

    def test_non_directory_entries_are_ignored(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        custom_dir = knowledge_root / "_custom_scenarios"
        custom_dir.mkdir(parents=True, exist_ok=True)
        (custom_dir / "README.md").write_text("not a scenario", encoding="utf-8")
        _write_valid_parametric_spec(knowledge_root, name="real_scenario")

        result = load_custom_scenarios_detailed(knowledge_root)

        assert "real_scenario" in result.loaded
        assert result.skipped == ()

    def test_spec_only_dir_reported_in_skipped(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_spec_only_agent_task(knowledge_root, name="spec_only_task")

        result = load_custom_scenarios_detailed(knowledge_root)

        assert "spec_only_task" not in result.loaded
        assert len(result.skipped) == 1
        entry = result.skipped[0]
        assert entry.name == "spec_only_task"
        assert "spec.json" in entry.reason
        assert "no compiled source" in entry.reason

    def test_spec_only_dir_emits_warning(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_spec_only_agent_task(knowledge_root, name="spec_only_task")

        with caplog.at_level(logging.WARNING, logger="autocontext.scenarios.custom.registry"):
            load_custom_scenarios_detailed(knowledge_root)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "spec_only_task" in message
        assert "spec.json" in message
        assert "no compiled source" in message
        assert "new-scenario --from-spec" in message
        assert "\n" not in message

    def test_truly_empty_dir_remains_silent(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        empty_dir = knowledge_root / "_custom_scenarios" / "empty_scenario"
        empty_dir.mkdir(parents=True, exist_ok=True)

        with caplog.at_level(logging.WARNING, logger="autocontext.scenarios.custom.registry"):
            result = load_custom_scenarios_detailed(knowledge_root)

        assert "empty_scenario" not in result.loaded
        assert all(e.name != "empty_scenario" for e in result.skipped)
        assert caplog.records == []

    def test_import_file_not_found_uses_real_failure_reason(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_agent_task_with_import_file_not_found(knowledge_root, name="broken_import_task")

        with caplog.at_level(logging.DEBUG, logger="autocontext.scenarios.custom.registry"):
            result = load_custom_scenarios_detailed(knowledge_root)

        assert "broken_import_task" not in result.loaded
        assert len(result.skipped) == 1
        entry = result.skipped[0]
        assert entry.name == "broken_import_task"
        assert "missing-data.txt" in entry.reason
        assert "no compiled source" not in entry.reason

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any(r.exc_text for r in debug_records), "import-time FileNotFoundError should retain DEBUG traceback"

    def test_ts_created_simulation_auto_materializes(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        scenario_dir = _write_ts_simulation_spec(knowledge_root, name="ts_simulation")

        loaded = load_all_custom_scenarios(knowledge_root)

        assert "ts_simulation" in loaded, f"expected ts_simulation in loaded, got {list(loaded.keys())}"
        assert (scenario_dir / "scenario.py").is_file(), "scenario.py should have been generated"

    def test_ts_created_investigation_auto_materializes(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        scenario_dir = _write_ts_investigation_spec(knowledge_root, name="ts_investigation")

        loaded = load_all_custom_scenarios(knowledge_root)

        assert "ts_investigation" in loaded
        assert (scenario_dir / "scenario.py").is_file()

    def test_auto_materialize_falls_back_on_bad_family_spec(
        self,
        tmp_path: Path,
    ) -> None:
        """spec.json exists for simulation family but is missing required fields."""
        knowledge_root = tmp_path / "knowledge"
        scenario_dir = knowledge_root / "_custom_scenarios" / "bad_sim"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "scenario_type.txt").write_text("simulation", encoding="utf-8")
        (scenario_dir / "spec.json").write_text(
            json.dumps(
                {
                    "name": "bad_sim",
                    "scenario_type": "simulation",
                    # Missing required simulation fields: environment_description, actions, etc.
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        result = load_custom_scenarios_detailed(knowledge_root)

        assert "bad_sim" not in result.loaded
        assert len(result.skipped) == 1
        assert result.skipped[0].name == "bad_sim"

    def test_parametric_auto_materialize_still_works(
        self,
        tmp_path: Path,
    ) -> None:
        """Existing parametric path must still work — regression guard for the refactor."""
        knowledge_root = tmp_path / "knowledge"
        scenario_dir = _write_valid_parametric_spec(knowledge_root, name="parametric_regression")

        loaded = load_all_custom_scenarios(knowledge_root)

        assert "parametric_regression" in loaded
        assert (scenario_dir / "scenario.py").is_file()
        scenario = loaded["parametric_regression"]()
        assert scenario.name == "parametric_regression"

    def test_reconstruct_handles_nested_pydantic_models(self) -> None:
        from autocontext.scenarios.custom.simulation_spec import SimulationSpec

        raw = {
            "description": "test",
            "environment_description": "env",
            "initial_state_description": "init",
            "success_criteria": ["win"],
            "failure_modes": ["lose"],
            "actions": [
                {
                    "name": "act1",
                    "description": "do thing",
                    "parameters": {},
                    "preconditions": ["ready"],
                    "effects": ["done"],
                },
            ],
            "max_steps": 3,
        }

        spec = _reconstruct_family_spec(SimulationSpec, raw)

        assert isinstance(spec, SimulationSpec)
        assert spec.description == "test"
        assert len(spec.actions) == 1
        assert spec.actions[0].name == "act1"
        assert spec.max_steps == 3

    def test_reconstruct_handles_missing_optional_fields(self) -> None:
        from autocontext.scenarios.custom.simulation_spec import SimulationSpec

        raw = {
            "description": "test",
            "environment_description": "env",
            "initial_state_description": "init",
            "success_criteria": ["win"],
            "failure_modes": ["lose"],
            "actions": [],
            # max_steps omitted — has default of 10
        }

        spec = _reconstruct_family_spec(SimulationSpec, raw)

        assert spec.max_steps == 10
