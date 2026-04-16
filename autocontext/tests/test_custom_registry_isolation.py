"""AC-563 Failure A: custom scenario registry isolation.

One malformed ``spec.json`` must not prevent the registry from loading other
scenarios, and must not dump a traceback into stderr for unrelated commands.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from autocontext.scenarios.custom.registry import load_all_custom_scenarios


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
                "strategy_interface_description": (
                    "Return JSON with a single float `bias` in [0,1]."
                ),
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
        assert len(warnings) == 1, (
            f"expected exactly one warning, got {[r.message for r in warnings]}"
        )
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
        assert any(r.exc_text for r in debug_records), (
            "at DEBUG level the full traceback must be available via exc_info"
        )
