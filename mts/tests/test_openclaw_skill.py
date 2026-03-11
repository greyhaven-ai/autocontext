"""Tests for AC-192: ClawHub skill wrapper for MTS scenarios and artifacts."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from mts.openclaw.models import (
    ArtifactSummary,
    EvaluationResult,
    ScenarioInfo,
    ScenarioRecommendation,
    SkillManifest,
)
from mts.openclaw.skill import MtsSkillWrapper

_REG = "mts.openclaw.skill.SCENARIO_REGISTRY"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _game() -> MagicMock:
    s = MagicMock()
    s.describe_rules.return_value = "Capture the flag on a grid."
    s.describe_strategy_interface.return_value = '{"aggression": float, "defense": float}'
    s.describe_evaluation_criteria.return_value = "Score = captures + survival"
    del s.get_task_prompt
    del s.get_rubric
    del s.describe_task
    return s


def _task() -> MagicMock:
    s = MagicMock()
    s.describe_task.return_value = "Write a summary of the document."
    s.get_rubric.return_value = "Clarity, completeness, accuracy"
    s.get_task_prompt.return_value = "Summarize the following..."
    del s.describe_rules
    del s.describe_strategy_interface
    del s.describe_evaluation_criteria
    del s.execute_match
    del s.validate_actions
    del s.initial_state
    return s


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.sqlite = MagicMock()
    ctx.artifacts = MagicMock()
    ctx.settings = MagicMock()
    return ctx


@pytest.fixture()
def reg() -> dict[str, Any]:
    return {"grid_ctf": lambda: _game(), "summarize_doc": lambda: _task()}


# ---------------------------------------------------------------------------
# TestModels
# ---------------------------------------------------------------------------


class TestModels:
    def test_scenario_info_minimal(self) -> None:
        info = ScenarioInfo(
            name="grid_ctf", display_name="Grid CTF",
            scenario_type="game", description="Capture flags", strategy_interface="{}",
        )
        assert info.name == "grid_ctf"
        assert info.scenario_type == "game"

    def test_scenario_info_rejects_bad_type(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioInfo(
                name="x", display_name="X", scenario_type="bad_type",  # type: ignore[arg-type]
                description="", strategy_interface="",
            )

    def test_evaluation_result_defaults(self) -> None:
        r = EvaluationResult(scenario_name="grid_ctf", strategy={"a": 1}, valid=True)
        assert r.scores == []
        assert r.mean_score == 0.0
        assert r.harness_passed is None

    def test_artifact_summary_validation(self) -> None:
        s = ArtifactSummary(id="abc", name="bounds", artifact_type="harness", scenario="grid_ctf", version=1)
        assert s.tags == []
        assert s.created_at == ""

    def test_skill_manifest_roundtrip(self) -> None:
        m = SkillManifest(
            version="0.1.0", description="test",
            scenarios=[ScenarioInfo(
                name="grid_ctf", display_name="Grid CTF",
                scenario_type="game", description="Flags", strategy_interface="{}",
            )],
            mcp_tools=["mts_list_scenarios"],
        )
        data = json.loads(m.model_dump_json())
        restored = SkillManifest.model_validate(data)
        assert restored.name == "mts"
        assert len(restored.scenarios) == 1

    def test_scenario_recommendation_fields(self) -> None:
        rec = ScenarioRecommendation(
            scenario_name="grid_ctf", confidence=0.85, reasoning="match", alternatives=[],
        )
        assert rec.confidence == 0.85


# ---------------------------------------------------------------------------
# TestSkillManifest
# ---------------------------------------------------------------------------


class TestSkillManifest:
    def test_manifest_has_version_and_name(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg):
            m = MtsSkillWrapper(_ctx()).manifest()
        assert m.name == "mts"
        assert m.version != ""
        assert "scenario_evaluation" in m.capabilities

    def test_manifest_lists_scenarios_with_types(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg):
            m = MtsSkillWrapper(_ctx()).manifest()
        names = {s.name for s in m.scenarios}
        assert "grid_ctf" in names
        assert "summarize_doc" in names
        assert next(s for s in m.scenarios if s.name == "grid_ctf").scenario_type == "game"
        assert next(s for s in m.scenarios if s.name == "summarize_doc").scenario_type == "agent_task"

    def test_manifest_includes_mcp_tools(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg):
            m = MtsSkillWrapper(_ctx()).manifest()
        assert len(m.mcp_tools) > 0
        assert "mts_list_scenarios" in m.mcp_tools

    def test_manifest_game_has_strategy_interface(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg):
            m = MtsSkillWrapper(_ctx()).manifest()
        game = next(s for s in m.scenarios if s.name == "grid_ctf")
        assert "aggression" in game.strategy_interface

    def test_manifest_empty_registry(self) -> None:
        with patch(_REG, {}):
            m = MtsSkillWrapper(_ctx()).manifest()
        assert m.scenarios == []


# ---------------------------------------------------------------------------
# TestDiscoverScenarios
# ---------------------------------------------------------------------------


class TestDiscoverScenarios:
    def test_all_scenarios_when_no_query(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg):
            results = MtsSkillWrapper(_ctx()).discover_scenarios()
        assert len(results) == 2

    def test_results_have_correct_types(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg):
            results = MtsSkillWrapper(_ctx()).discover_scenarios()
        types = {r.scenario_type for r in results}
        assert "game" in types
        assert "agent_task" in types

    def test_query_filters_by_relevance(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.search_strategies") as mock_search:
            mock_search.return_value = [MagicMock(scenario_name="grid_ctf", relevance_score=0.8)]
            results = MtsSkillWrapper(_ctx()).discover_scenarios(query="grid capture flag")
        assert results[0].name == "grid_ctf"

    def test_query_ranks_unsolved_registry_scenarios(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.search_strategies") as mock_search:
            mock_search.return_value = []
            results = MtsSkillWrapper(_ctx()).discover_scenarios(query="summary document writing")
        assert results[0].name == "summarize_doc"

    def test_query_no_match_returns_all(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.search_strategies") as mock_search:
            mock_search.return_value = []
            results = MtsSkillWrapper(_ctx()).discover_scenarios(query="zzz_unknown")
        assert len(results) == 2


# ---------------------------------------------------------------------------
# TestSelectScenario
# ---------------------------------------------------------------------------


class TestSelectScenario:
    def test_returns_best_match(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.search_strategies") as mock_search:
            mock_search.return_value = [
                MagicMock(scenario_name="grid_ctf", relevance_score=0.9, match_reason="'grid' in name"),
            ]
            rec = MtsSkillWrapper(_ctx()).select_scenario("grid based game")
        assert rec.scenario_name == "grid_ctf"
        assert rec.confidence > 0

    def test_alternatives_populated(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.search_strategies") as mock_search:
            mock_search.return_value = [
                MagicMock(scenario_name="grid_ctf", relevance_score=0.9, match_reason="match"),
                MagicMock(scenario_name="summarize_doc", relevance_score=0.3, match_reason="match"),
            ]
            rec = MtsSkillWrapper(_ctx()).select_scenario("grid based game")
        assert rec.scenario_name == "grid_ctf"
        assert len(rec.alternatives) >= 1

    def test_fallback_when_no_results(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.search_strategies") as mock_search:
            mock_search.return_value = []
            rec = MtsSkillWrapper(_ctx()).select_scenario("something unknown")
        assert rec.scenario_name in ("grid_ctf", "summarize_doc")
        assert rec.confidence == 0.0

    def test_confidence_from_relevance(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.search_strategies") as mock_search:
            mock_search.return_value = [
                MagicMock(scenario_name="grid_ctf", relevance_score=0.75, match_reason="keyword"),
            ]
            rec = MtsSkillWrapper(_ctx()).select_scenario("grid")
        assert rec.confidence == pytest.approx(0.75)

    def test_select_scenario_uses_registry_ranking_when_search_index_empty(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.search_strategies") as mock_search:
            mock_search.return_value = []
            rec = MtsSkillWrapper(_ctx()).select_scenario("write a summary of a document")
        assert rec.scenario_name == "summarize_doc"
        assert rec.confidence > 0.0


# ---------------------------------------------------------------------------
# TestEvaluate
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_valid_strategy_returns_scores(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.validate_strategy_against_harness") as mv, \
             patch("mts.openclaw.skill.evaluate_strategy") as me:
            mv.return_value = {"valid": True, "reason": "ok", "harness_passed": True, "harness_errors": []}
            me.return_value = {"scores": [0.7, 0.8, 0.9], "mean_score": 0.8, "best_score": 0.9}
            result = MtsSkillWrapper(_ctx()).evaluate("grid_ctf", {"aggression": 0.5})
        assert result.valid is True
        assert result.mean_score == pytest.approx(0.8)
        assert result.best_score == pytest.approx(0.9)
        assert result.scores == [0.7, 0.8, 0.9]

    def test_invalid_strategy(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.validate_strategy_against_harness") as mv:
            mv.return_value = {
                "valid": False, "reason": "aggression out of range",
                "harness_passed": None, "harness_errors": [],
            }
            result = MtsSkillWrapper(_ctx()).evaluate("grid_ctf", {"aggression": 99.0})
        assert result.valid is False
        assert "aggression" in result.validation_errors[0]
        assert result.scores == []

    def test_harness_results_included(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.validate_strategy_against_harness") as mv, \
             patch("mts.openclaw.skill.evaluate_strategy") as me:
            mv.return_value = {
                "valid": True, "reason": "ok",
                "harness_passed": False, "harness_errors": ["bounds_check failed"],
            }
            me.return_value = {"scores": [0.5], "mean_score": 0.5, "best_score": 0.5}
            result = MtsSkillWrapper(_ctx()).evaluate("grid_ctf", {"aggression": 0.5})
        assert result.harness_passed is False
        assert "bounds_check failed" in result.harness_errors

    def test_unknown_scenario_error(self) -> None:
        with patch(_REG, {}):
            result = MtsSkillWrapper(_ctx()).evaluate("nonexistent", {"a": 1})
        assert result.valid is False
        assert any("not found" in e.lower() for e in result.validation_errors)

    def test_agent_task_evaluation_returns_explicit_error(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg):
            result = MtsSkillWrapper(_ctx()).evaluate("summarize_doc", {"output": "summary"})
        assert result.valid is False
        assert any("agent task" in e.lower() for e in result.validation_errors)

    def test_result_has_all_fields(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.validate_strategy_against_harness") as mv, \
             patch("mts.openclaw.skill.evaluate_strategy") as me:
            mv.return_value = {"valid": True, "reason": "", "harness_passed": True, "harness_errors": []}
            me.return_value = {"scores": [0.6], "mean_score": 0.6, "best_score": 0.6}
            result = MtsSkillWrapper(_ctx()).evaluate("grid_ctf", {"x": 1})
        assert isinstance(result, EvaluationResult)
        data = result.model_dump()
        assert "scenario_name" in data
        assert "scores" in data

    def test_evaluate_propagates_runtime_errors(self, reg: dict[str, Any]) -> None:
        with patch(_REG, reg), \
             patch("mts.openclaw.skill.validate_strategy_against_harness") as mv, \
             patch("mts.openclaw.skill.evaluate_strategy") as me:
            mv.return_value = {"valid": True, "reason": "", "harness_passed": True, "harness_errors": []}
            me.return_value = {"error": "evaluation failed"}
            result = MtsSkillWrapper(_ctx()).evaluate("grid_ctf", {"x": 1})
        assert result.valid is False
        assert result.validation_errors == ["evaluation failed"]


# ---------------------------------------------------------------------------
# TestDiscoverArtifacts
# ---------------------------------------------------------------------------


class TestDiscoverArtifacts:
    def test_returns_all_when_no_filters(self) -> None:
        with patch("mts.openclaw.skill.list_artifacts") as ml:
            ml.return_value = [
                {"id": "a1", "name": "bounds", "artifact_type": "harness", "scenario": "grid_ctf",
                 "version": 1, "tags": ["v1"], "created_at": "2026-01-01"},
                {"id": "a2", "name": "policy1", "artifact_type": "policy", "scenario": "othello",
                 "version": 2, "tags": [], "created_at": "2026-01-02"},
            ]
            results = MtsSkillWrapper(_ctx()).discover_artifacts()
        assert len(results) == 2
        assert all(isinstance(r, ArtifactSummary) for r in results)

    def test_passes_filters(self) -> None:
        with patch("mts.openclaw.skill.list_artifacts") as ml:
            ml.return_value = [
                {"id": "a1", "name": "bounds", "artifact_type": "harness", "scenario": "grid_ctf",
                 "version": 1, "tags": [], "created_at": ""},
            ]
            wrapper = MtsSkillWrapper(_ctx())
            wrapper.discover_artifacts(scenario="grid_ctf", artifact_type="harness")
            ml.assert_called_once_with(wrapper.ctx, scenario="grid_ctf", artifact_type="harness")

    def test_empty_list(self) -> None:
        with patch("mts.openclaw.skill.list_artifacts") as ml:
            ml.return_value = []
            results = MtsSkillWrapper(_ctx()).discover_artifacts()
        assert results == []
