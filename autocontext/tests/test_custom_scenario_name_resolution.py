from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from autocontext.config.settings import AppSettings
from autocontext.loop.generation_runner import GenerationRunner
from autocontext.scenarios.custom.registry import load_all_custom_scenarios


def _settings(tmp_path: Path, knowledge_root: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=knowledge_root,
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        agent_provider="deterministic",
        judge_provider="anthropic",
        anthropic_api_key="test-key",
    )


def _write_parametric_custom_spec(knowledge_root: Path, name: str = "linear_outage_escalation") -> Path:
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "spec.json").write_text(
        json.dumps(
            {
                "name": name,
                "display_name": "Linear Outage Escalation",
                "description": "Escalate likely Linear outages while avoiding unnecessary paging.",
                "strategy_interface_description": (
                    "Return JSON with clarification_threshold and escalation_bias floats in [0,1]."
                ),
                "evaluation_criteria": "Reward correct outage escalation timing.",
                "strategy_params": [
                    {
                        "name": "clarification_threshold",
                        "description": "How much clarification to gather before escalating.",
                        "min_value": 0.0,
                        "max_value": 1.0,
                        "default": 0.4,
                    },
                    {
                        "name": "escalation_bias",
                        "description": "How quickly to escalate a likely outage.",
                        "min_value": 0.0,
                        "max_value": 1.0,
                        "default": 0.6,
                    },
                ],
                "constraints": [
                    {
                        "expression": "clarification_threshold + escalation_bias",
                        "operator": "<=",
                        "threshold": 1.5,
                        "description": "Do not over-index on both clarification and escalation.",
                    }
                ],
                "environment_variables": [
                    {
                        "name": "incident_severity",
                        "description": "Severity of the underlying outage.",
                        "low": 0.2,
                        "high": 0.95,
                    }
                ],
                "scoring_components": [
                    {
                        "name": "outage_capture",
                        "description": "Ability to escalate real outages quickly.",
                        "formula_terms": {
                            "clarification_threshold": -0.1,
                            "escalation_bias": 0.7,
                        },
                        "noise_range": [0.0, 0.0],
                    }
                ],
                "final_score_weights": {"outage_capture": 1.0},
                "win_threshold": 0.5,
                "observation_constraints": [
                    "Ask targeted questions when ambiguity is high.",
                ],
                "scenario_type": "parametric",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return scenario_dir


class TestCustomScenarioNameResolution:
    def test_load_all_custom_scenarios_materializes_spec_only_parametric_scenario(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        scenario_dir = _write_parametric_custom_spec(knowledge_root)

        loaded = load_all_custom_scenarios(knowledge_root)

        assert "linear_outage_escalation" in loaded
        scenario_cls = loaded["linear_outage_escalation"]
        scenario = scenario_cls()
        assert scenario.name == "linear_outage_escalation"
        assert (scenario_dir / "scenario.py").is_file()

        result = scenario.execute_match(
            {
                "clarification_threshold": 0.4,
                "escalation_bias": 0.6,
            },
            seed=0,
        )
        assert result.validation_errors == []
        assert 0.0 <= result.score <= 1.0
        assert "Linear Outage Escalation" in result.summary

    def test_generation_runner_reload_resolves_saved_parametric_scenario_by_name(
        self,
        tmp_path: Path,
    ) -> None:
        knowledge_root = tmp_path / "knowledge"
        _write_parametric_custom_spec(knowledge_root)

        runner = GenerationRunner.__new__(GenerationRunner)
        runner.settings = _settings(tmp_path, knowledge_root)

        with patch.dict("autocontext.loop.generation_runner.SCENARIO_REGISTRY", {}, clear=True):
            scenario = GenerationRunner._scenario(runner, "linear_outage_escalation")

        result = scenario.execute_match(
            {
                "clarification_threshold": 0.35,
                "escalation_bias": 0.65,
            },
            seed=1,
        )
        assert scenario.name == "linear_outage_escalation"
        assert result.validation_errors == []
        assert result.score > 0.0
