from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.subagent_runtime import SubagentRuntime
from autocontext.scenarios.custom.creator import ScenarioCreator
from autocontext.scenarios.custom.designer import SPEC_END, SPEC_START, parse_spec_from_response
from autocontext.scenarios.custom.spec import ScenarioSpec


@pytest.fixture
def creator(tmp_path: Path) -> ScenarioCreator:
    client = DeterministicDevClient()
    runtime = SubagentRuntime(client)
    return ScenarioCreator(runtime=runtime, model="test-model", knowledge_root=tmp_path)


class TestDeriveName:
    def test_simple(self, creator: ScenarioCreator) -> None:
        name = creator.derive_name("A tower defense game")
        assert "_" in name or name.isalpha()
        assert name.islower() or "_" in name

    def test_strips_filler_words(self, creator: ScenarioCreator) -> None:
        name = creator.derive_name("A game where you balance the economy and defense")
        assert "a" != name.split("_")[0]
        assert "the" not in name.split("_")

    def test_max_three_words(self, creator: ScenarioCreator) -> None:
        name = creator.derive_name("very complex multi word resource management trading simulation")
        assert len(name.split("_")) <= 3


class TestDeriveNameImproved:
    def test_prefers_longer_words(self, creator: ScenarioCreator) -> None:
        """Longer words should sort first as they're more domain-specific."""
        name = creator.derive_name("a game where you manage resources efficiently")
        words = name.split("_")
        assert "efficiently" in words or "resources" in words

    def test_filters_expanded_stop_words(self, creator: ScenarioCreator) -> None:
        """Words like 'create', 'build', 'implement' should be filtered."""
        name = creator.derive_name("create a system to build agents that implement tools")
        assert "create" not in name.split("_")
        assert "build" not in name.split("_")
        assert "implement" not in name.split("_")


class TestParseSpecFromResponse:
    def test_valid_response(self) -> None:
        spec_data = {
            "name": "test",
            "display_name": "Test",
            "description": "Test scenario.",
            "strategy_interface_description": "Test interface.",
            "evaluation_criteria": "Test criteria.",
            "strategy_params": [
                {"name": "alpha", "description": "A", "min_value": 0.0, "max_value": 1.0, "default": 0.5},
            ],
            "constraints": [],
            "environment_variables": [
                {"name": "env", "description": "E", "low": 0.1, "high": 0.9},
            ],
            "scoring_components": [
                {"name": "score", "description": "S", "formula_terms": {"alpha": 1.0}, "noise_range": [-0.05, 0.05]},
            ],
            "final_score_weights": {"score": 1.0},
            "win_threshold": 0.5,
            "observation_constraints": [],
        }
        text = f"Some preamble.\n\n{SPEC_START}\n{json.dumps(spec_data)}\n{SPEC_END}\n\nSome epilogue."
        spec = parse_spec_from_response(text)
        assert spec.name == "test"

    def test_missing_delimiters(self) -> None:
        with pytest.raises(ValueError, match="delimiters"):
            parse_spec_from_response("no delimiters here")

    def test_invalid_json(self) -> None:
        text = f"{SPEC_START}\nnot valid json\n{SPEC_END}"
        with pytest.raises(json.JSONDecodeError):
            parse_spec_from_response(text)


class TestGenerateSpec:
    def test_deterministic_client(self, creator: ScenarioCreator) -> None:
        spec = creator.generate_spec("A resource management game")
        assert isinstance(spec, ScenarioSpec)
        assert spec.name
        assert len(spec.strategy_params) >= 2
        assert len(spec.scoring_components) >= 2
        assert abs(sum(spec.final_score_weights.values()) - 1.0) < 0.01

    def test_spec_is_valid(self, creator: ScenarioCreator) -> None:
        from autocontext.scenarios.custom.validator import validate_spec

        spec = creator.generate_spec("A simple strategy game")
        errors = validate_spec(spec)
        assert errors == []


class TestBuildAndValidate:
    def test_full_pipeline(self, creator: ScenarioCreator) -> None:
        spec = creator.generate_spec("A resource balance game")
        result = creator.build_and_validate(spec)
        assert result.scenario_class is not None
        assert len(result.test_scores) == 3
        for score in result.test_scores:
            assert 0.0 <= score <= 1.0

    def test_persists_to_disk(self, creator: ScenarioCreator) -> None:
        spec = creator.generate_spec("A resource allocation game")
        creator.build_and_validate(spec)
        scenario_dir = creator.knowledge_root / "_custom_scenarios" / spec.name
        assert (scenario_dir / "scenario.py").exists()
        assert (scenario_dir / "spec.json").exists()

    def test_loaded_class_in_sys_modules(self, creator: ScenarioCreator) -> None:
        spec = creator.generate_spec("A test scenario game")
        creator.build_and_validate(spec)
        module_name = f"autocontext.scenarios.custom.generated.{spec.name}"
        assert module_name in sys.modules

    def test_scenario_runs_match(self, creator: ScenarioCreator) -> None:
        spec = creator.generate_spec("A basic strategy game")
        result = creator.build_and_validate(spec)
        instance = result.scenario_class()
        default_strategy = {p.name: p.default for p in spec.strategy_params}
        match_result = instance.execute_match(strategy=default_strategy, seed=999)
        assert 0.0 <= match_result.score <= 1.0
        assert match_result.winner in ("challenger", "incumbent")


class TestEndToEnd:
    def test_description_to_match(self, creator: ScenarioCreator) -> None:
        spec = creator.generate_spec("A resource management game where you balance mining vs defense vs trade")
        assert spec.name
        result = creator.build_and_validate(spec)
        instance = result.scenario_class()

        default_strategy = {p.name: p.default for p in spec.strategy_params}
        match = instance.execute_match(strategy=default_strategy, seed=42)
        assert 0.0 <= match.score <= 1.0
        assert match.summary
        assert isinstance(match.replay, list)

    def test_revise_spec(self, creator: ScenarioCreator) -> None:
        spec = creator.generate_spec("A trading game")
        # Revise returns a valid spec (deterministic client returns same thing)
        revised = creator.revise_spec(spec, "Add more parameters")
        assert isinstance(revised, ScenarioSpec)
        assert revised.name
