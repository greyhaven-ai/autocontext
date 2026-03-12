"""Tests for the thin SDK client (AC-187)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autocontext.config import AppSettings
from autocontext.sdk import AutoContext
from autocontext.sdk_models import EvaluateResult, MatchResult, SearchResult, ValidateResult

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestMTSInit:
    """AutoContext client initialization and settings management."""

    def test_creates_with_defaults(self, tmp_path: Path) -> None:
        """Constructor works without any arguments, using AppSettings defaults."""
        db = tmp_path / "autocontext.sqlite3"
        client = AutoContext(db_path=db, knowledge_root=tmp_path / "knowledge")
        assert client._ctx is not None
        assert isinstance(client._settings, AppSettings)

    def test_creates_with_path_overrides(self, tmp_path: Path) -> None:
        """Path overrides propagate to internal settings."""
        db = tmp_path / "test.db"
        kr = tmp_path / "k"
        sr = tmp_path / "s"
        csp = tmp_path / "cs"
        client = AutoContext(db_path=db, knowledge_root=kr, skills_root=sr, claude_skills_path=csp)
        assert client._settings.db_path == db
        assert client._settings.knowledge_root == kr
        assert client._settings.skills_root == sr
        assert client._settings.claude_skills_path == csp

    def test_accepts_extra_settings_overrides(self, tmp_path: Path) -> None:
        """Arbitrary AppSettings fields can be overridden via kwargs."""
        client = AutoContext(db_path=tmp_path / "autocontext.db", matches_per_generation=7)
        assert client._settings.matches_per_generation == 7

    def test_reuses_settings_object(self, tmp_path: Path) -> None:
        """If an AppSettings is passed directly, it is used without modification."""
        settings = AppSettings(db_path=tmp_path / "autocontext.db")
        client = AutoContext(settings=settings)
        assert client._settings.db_path == settings.db_path

    def test_uses_load_settings_as_base(self, tmp_path: Path) -> None:
        base = AppSettings(db_path=tmp_path / "base.db", knowledge_root=tmp_path / "base_k")
        with patch("autocontext.sdk.load_settings", return_value=base):
            client = AutoContext(db_path=tmp_path / "override.db")
        assert client._settings.db_path == tmp_path / "override.db"
        assert client._settings.knowledge_root == tmp_path / "base_k"


# ---------------------------------------------------------------------------
# Scenario discovery
# ---------------------------------------------------------------------------


class TestListScenarios:
    """list_scenarios delegates to tools.list_scenarios."""

    def test_returns_scenario_list(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.list_scenarios", return_value=[
            {"name": "grid_ctf", "rules_preview": "Capture the flag..."},
        ]) as mock_ls:
            result = client.list_scenarios()
        mock_ls.assert_called_once()
        assert len(result) == 1
        assert result[0]["name"] == "grid_ctf"


class TestDescribeScenario:
    """describe_scenario delegates to tools.describe_scenario."""

    def test_returns_description_dict(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.describe_scenario", return_value={
            "rules": "Some rules",
            "strategy_interface": "aggression, defense",
            "evaluation_criteria": "score",
        }) as mock_ds:
            result = client.describe_scenario("grid_ctf")
        mock_ds.assert_called_once_with("grid_ctf")
        assert result["rules"] == "Some rules"


# ---------------------------------------------------------------------------
# Strategy evaluation
# ---------------------------------------------------------------------------


class TestValidate:
    """validate delegates to the shared harness-aware validation path."""

    def test_valid_strategy_returns_typed_result(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": True, "reason": "ok", "harness_passed": True, "harness_errors": [],
        }):
            result = client.validate("grid_ctf", {"aggression": 0.5})
        assert isinstance(result, ValidateResult)
        assert result.valid is True
        assert result.reason == "ok"

    def test_invalid_strategy_returns_typed_result(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": False, "reason": "out of range", "harness_passed": False, "harness_errors": [],
        }):
            result = client.validate("grid_ctf", {"aggression": 5.0})
        assert isinstance(result, ValidateResult)
        assert result.valid is False
        assert "out of range" in result.reason

    def test_unknown_scenario_error_returns_invalid_result(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "error": "Unknown scenario 'grid_ctf'",
        }):
            result = client.validate("grid_ctf", {"aggression": 0.5})
        assert result.valid is False
        assert "Unknown scenario" in result.reason


class TestEvaluate:
    """evaluate delegates to the shared validation/evaluation path."""

    def test_returns_typed_evaluate_result(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": True, "reason": "ok", "harness_passed": True, "harness_errors": [],
        }), patch("autocontext.sdk.tools.evaluate_strategy", return_value={
            "scenario": "grid_ctf",
            "matches": 3,
            "scores": [0.6, 0.7, 0.8],
            "mean_score": 0.7,
            "best_score": 0.8,
        }):
            result = client.evaluate("grid_ctf", {"aggression": 0.5}, matches=3)
        assert isinstance(result, EvaluateResult)
        assert result.scores == [0.6, 0.7, 0.8]
        assert result.mean_score == pytest.approx(0.7)
        assert result.best_score == pytest.approx(0.8)

    def test_evaluate_passes_matches_and_seed(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": True, "reason": "", "harness_passed": True, "harness_errors": [],
        }), patch("autocontext.sdk.tools.evaluate_strategy", return_value={
            "scenario": "grid_ctf",
            "matches": 5,
            "scores": [0.5] * 5,
            "mean_score": 0.5,
            "best_score": 0.5,
        }) as mock_eval:
            client.evaluate("grid_ctf", {"aggression": 0.5}, matches=5, seed_base=100)
        mock_eval.assert_called_once_with("grid_ctf", {"aggression": 0.5}, num_matches=5, seed_base=100)

    def test_evaluate_error_propagates(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": True, "reason": "", "harness_passed": True, "harness_errors": [],
        }), patch("autocontext.sdk.tools.evaluate_strategy", return_value={
            "error": "Agent task scenarios use judge evaluation",
        }):
            result = client.evaluate("some_task", {})
        assert isinstance(result, EvaluateResult)
        assert result.error == "Agent task scenarios use judge evaluation"

    def test_evaluate_invalid_strategy_stops_before_tournament(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": False, "reason": "out of range", "harness_passed": False, "harness_errors": [],
        }), patch("autocontext.sdk.tools.evaluate_strategy") as mock_eval:
            result = client.evaluate("grid_ctf", {"aggression": 5.0})
        mock_eval.assert_not_called()
        assert result.error == "out of range"


class TestMatch:
    """match validates first and then delegates to tools.run_match."""

    def test_returns_typed_match_result(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": True, "reason": "", "harness_passed": True, "harness_errors": [],
        }), patch("autocontext.sdk.tools.run_match", return_value={
            "score": 0.75,
            "winner": "challenger",
            "summary": "Challenger captured the flag",
            "metrics": {"turns": 10},
            "replay": [],
        }):
            result = client.match("grid_ctf", {"aggression": 0.5}, seed=42)
        assert isinstance(result, MatchResult)
        assert result.score == pytest.approx(0.75)
        assert result.winner == "challenger"
        assert result.summary == "Challenger captured the flag"
        assert result.metrics == {"turns": 10}

    def test_match_passes_seed(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": True, "reason": "", "harness_passed": True, "harness_errors": [],
        }), patch("autocontext.sdk.tools.run_match", return_value={
            "score": 0.5, "winner": "defender", "summary": "draw", "metrics": {}, "replay": [],
        }) as mock_rm:
            client.match("grid_ctf", {"aggression": 0.5}, seed=99)
        mock_rm.assert_called_once_with("grid_ctf", {"aggression": 0.5}, seed=99)

    def test_match_invalid_strategy_stops_before_execution(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.validate_strategy_against_harness", return_value={
            "valid": False, "reason": "out of range", "harness_passed": False, "harness_errors": [],
        }), patch("autocontext.sdk.tools.run_match") as mock_rm:
            result = client.match("grid_ctf", {"aggression": 5.0})
        mock_rm.assert_not_called()
        assert result.error == "out of range"


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------


class TestSearch:
    """search delegates to tools.search_strategies and returns typed SearchResult list."""

    def test_returns_typed_search_results(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.search_strategies", return_value=[
            {
                "scenario": "grid_ctf",
                "display_name": "Grid Ctf",
                "description": "A capture-the-flag game",
                "relevance": 0.85,
                "best_score": 0.9,
                "best_elo": 1600.0,
                "match_reason": "'grid' in name",
            },
        ]):
            results = client.search("grid strategy", top_k=3)
        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].scenario_name == "grid_ctf"
        assert results[0].relevance == pytest.approx(0.85)

    def test_search_passes_top_k(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.search_strategies", return_value=[]) as mock_ss:
            client.search("anything", top_k=10)
        mock_ss.assert_called_once_with(client._ctx, "anything", 10)

    def test_search_empty_results(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.search_strategies", return_value=[]):
            results = client.search("nonexistent")
        assert results == []


class TestExport:
    """export_skill and export_package delegate to tool functions."""

    def test_export_skill_returns_dict(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.export_skill", return_value={
            "scenario_name": "grid_ctf",
            "playbook": "some playbook",
        }):
            result = client.export_skill("grid_ctf")
        assert result["scenario_name"] == "grid_ctf"

    def test_export_package_returns_dict(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.export_package", return_value={
            "scenario": "grid_ctf",
            "version": "1.0.0",
        }):
            result = client.export_package("grid_ctf")
        assert result["scenario"] == "grid_ctf"


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class TestListArtifacts:
    """list_artifacts delegates with correct filters."""

    def test_list_all_artifacts(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.list_artifacts", return_value=[
            {"id": "abc", "name": "test", "artifact_type": "harness", "scenario": "grid_ctf", "version": 1},
        ]) as mock_la:
            result = client.list_artifacts()
        mock_la.assert_called_once_with(client._ctx, scenario=None, artifact_type=None)
        assert len(result) == 1

    def test_list_artifacts_with_filters(self, tmp_path: Path) -> None:
        client = AutoContext(db_path=tmp_path / "autocontext.db", knowledge_root=tmp_path / "k")
        with patch("autocontext.sdk.tools.list_artifacts", return_value=[]) as mock_la:
            client.list_artifacts(scenario="grid_ctf", artifact_type="policy")
        mock_la.assert_called_once_with(client._ctx, scenario="grid_ctf", artifact_type="policy")


# ---------------------------------------------------------------------------
# Public API export
# ---------------------------------------------------------------------------


class TestPublicAPI:
    """AutoContext is importable from the package root."""

    def test_import_from_autocontext(self) -> None:
        from autocontext import AutoContext as AutoContext_root
        assert AutoContext_root is AutoContext
