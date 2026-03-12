"""Tests for AC-163: HarnessCoverageAnalyzer for measuring harness protection level.

Tests the HarnessCoverage dataclass, HarnessCoverageAnalyzer weighted scoring,
partial harness coverage, validation accuracy impact, and model tier recommendations.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autocontext.execution.harness_coverage import HarnessCoverage, HarnessCoverageAnalyzer
from autocontext.execution.harness_loader import HarnessLoader

# ── Helpers ─────────────────────────────────────────────────────────────


def _mock_loader(
    *,
    names: list[str] | None = None,
    callables: dict[str, list[str]] | None = None,
) -> MagicMock:
    """Build a mock HarnessLoader with controllable callables.

    Args:
        names: List of loaded harness file names.
        callables: Mapping of file_name -> list of function names available.
    """
    loader = MagicMock()
    names = names or []
    callables = callables or {}
    loader.loaded_names = names

    def _has_callable(file_name: str, fn_name: str) -> bool:
        return fn_name in callables.get(file_name, [])

    loader.has_callable.side_effect = _has_callable
    return loader


# ── HarnessCoverage dataclass tests ─────────────────────────────────────


class TestHarnessCoverage:
    def test_dataclass_fields(self) -> None:
        cov = HarnessCoverage(
            has_validate_strategy=True,
            has_enumerate_legal_actions=False,
            has_parse_game_state=False,
            has_is_legal_action=False,
            validation_accuracy=0.95,
            function_count=1,
            coverage_score=0.4,
        )
        assert cov.has_validate_strategy is True
        assert cov.has_enumerate_legal_actions is False
        assert cov.validation_accuracy == 0.95
        assert cov.function_count == 1
        assert cov.coverage_score == 0.4

    def test_frozen(self) -> None:
        cov = HarnessCoverage(
            has_validate_strategy=True,
            has_enumerate_legal_actions=False,
            has_parse_game_state=False,
            has_is_legal_action=False,
            validation_accuracy=0.0,
            function_count=0,
            coverage_score=0.0,
        )
        try:
            cov.coverage_score = 1.0  # type: ignore[misc]
            raise AssertionError("should be frozen")
        except AttributeError:
            pass  # expected


# ── Analyzer scoring tests ──────────────────────────────────────────────


class TestAnalyzerScoring:
    def test_empty_loader_zero_coverage(self) -> None:
        """No loaded harness files → coverage_score 0.0."""
        loader = _mock_loader(names=[], callables={})
        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader)
        assert cov.coverage_score == 0.0
        assert cov.function_count == 0

    def test_full_coverage_with_perfect_accuracy(self) -> None:
        """All 4 functions present + 1.0 accuracy → coverage_score 1.0."""
        loader = _mock_loader(
            names=["validator"],
            callables={"validator": [
                "validate_strategy",
                "enumerate_legal_actions",
                "is_legal_action",
                "parse_game_state",
            ]},
        )
        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader, validation_accuracy=1.0)
        assert abs(cov.coverage_score - 1.0) < 1e-9
        assert cov.has_validate_strategy is True
        assert cov.has_enumerate_legal_actions is True
        assert cov.has_is_legal_action is True
        assert cov.has_parse_game_state is True
        assert cov.function_count == 4

    def test_validate_strategy_only(self) -> None:
        """Only validate_strategy → weighted score of 0.4 * accuracy_factor."""
        loader = _mock_loader(
            names=["v"],
            callables={"v": ["validate_strategy"]},
        )
        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader, validation_accuracy=1.0)
        assert cov.has_validate_strategy is True
        assert cov.has_enumerate_legal_actions is False
        assert cov.coverage_score == 0.4

    def test_enumerate_legal_actions_only(self) -> None:
        """Only enumerate_legal_actions → weighted score of 0.3 * accuracy_factor."""
        loader = _mock_loader(
            names=["v"],
            callables={"v": ["enumerate_legal_actions"]},
        )
        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader, validation_accuracy=1.0)
        assert cov.coverage_score == 0.3

    def test_accuracy_scales_score(self) -> None:
        """Validation accuracy multiplies the raw coverage score."""
        loader = _mock_loader(
            names=["v"],
            callables={"v": ["validate_strategy", "enumerate_legal_actions"]},
        )
        analyzer = HarnessCoverageAnalyzer()
        # Raw = 0.4 + 0.3 = 0.7, accuracy = 0.5 → 0.7 * 0.5 = 0.35
        cov = analyzer.analyze(loader, validation_accuracy=0.5)
        assert abs(cov.coverage_score - 0.35) < 1e-9

    def test_zero_accuracy_uses_half_penalty(self) -> None:
        """When validation_accuracy=0.0, raw score is halved (0.5 penalty)."""
        loader = _mock_loader(
            names=["v"],
            callables={"v": ["validate_strategy"]},
        )
        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader, validation_accuracy=0.0)
        # Raw = 0.4, penalty = 0.5 → 0.4 * 0.5 = 0.2
        assert abs(cov.coverage_score - 0.2) < 1e-9

    def test_coverage_capped_at_one(self) -> None:
        """Coverage score should never exceed 1.0."""
        loader = _mock_loader(
            names=["v"],
            callables={"v": [
                "validate_strategy",
                "enumerate_legal_actions",
                "is_legal_action",
                "parse_game_state",
            ]},
        )
        analyzer = HarnessCoverageAnalyzer()
        # Even with accuracy > 1.0 somehow, score caps at 1.0
        cov = analyzer.analyze(loader, validation_accuracy=1.5)
        assert cov.coverage_score <= 1.0

    def test_multiple_harness_files(self) -> None:
        """Functions spread across multiple files still count."""
        loader = _mock_loader(
            names=["a", "b"],
            callables={
                "a": ["validate_strategy"],
                "b": ["enumerate_legal_actions", "is_legal_action"],
            },
        )
        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader, validation_accuracy=1.0)
        assert cov.has_validate_strategy is True
        assert cov.has_enumerate_legal_actions is True
        assert cov.has_is_legal_action is True
        assert cov.has_parse_game_state is False
        # 0.4 + 0.3 + 0.2 = 0.9
        assert abs(cov.coverage_score - 0.9) < 1e-9
        assert cov.function_count == 3

    def test_default_accuracy_is_zero(self) -> None:
        """Default validation_accuracy parameter should be 0.0."""
        loader = _mock_loader(
            names=["v"],
            callables={"v": ["validate_strategy"]},
        )
        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader)
        # Raw = 0.4, accuracy = 0.0 → penalty of 0.5 → 0.4 * 0.5 = 0.2
        assert abs(cov.coverage_score - 0.2) < 1e-9

    def test_function_count_counts_detected_functions_not_files(self) -> None:
        loader = _mock_loader(
            names=["a", "b", "c"],
            callables={
                "a": ["validate_strategy"],
                "b": [],
                "c": ["parse_game_state"],
            },
        )
        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader, validation_accuracy=1.0)
        assert cov.function_count == 2


class TestAnalyzerWithRealLoader:
    def test_real_loader_detects_is_legal_action(self, tmp_path: Path) -> None:
        harness_dir = tmp_path / "harness"
        harness_dir.mkdir()
        (harness_dir / "validator.py").write_text(
            "\n".join(
                [
                    "def validate_strategy(strategy, scenario):",
                    "    return True, []",
                    "",
                    "def enumerate_legal_actions(state):",
                    "    return []",
                    "",
                    "def parse_game_state(raw):",
                    "    return raw",
                    "",
                    "def is_legal_action(state, action):",
                    "    return True",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        loader = HarnessLoader(harness_dir)
        assert loader.load() == ["validator"]

        analyzer = HarnessCoverageAnalyzer()
        cov = analyzer.analyze(loader, validation_accuracy=1.0)
        assert cov.has_is_legal_action is True
        assert cov.function_count == 4


# ── Model tier recommendation tests ─────────────────────────────────────


class TestModelTierRecommendation:
    def test_high_coverage_recommends_haiku(self) -> None:
        """coverage_score >= 0.9 → haiku."""
        analyzer = HarnessCoverageAnalyzer()
        cov = HarnessCoverage(
            has_validate_strategy=True, has_enumerate_legal_actions=True,
            has_parse_game_state=True, has_is_legal_action=True,
            validation_accuracy=1.0, function_count=1, coverage_score=0.95,
        )
        assert analyzer.recommend_model_tier(cov) == "haiku"

    def test_medium_coverage_recommends_sonnet(self) -> None:
        """0.5 <= coverage_score < 0.9 → sonnet."""
        analyzer = HarnessCoverageAnalyzer()
        cov = HarnessCoverage(
            has_validate_strategy=True, has_enumerate_legal_actions=True,
            has_parse_game_state=False, has_is_legal_action=False,
            validation_accuracy=0.8, function_count=1, coverage_score=0.6,
        )
        assert analyzer.recommend_model_tier(cov) == "sonnet"

    def test_low_coverage_returns_empty(self) -> None:
        """coverage_score < 0.5 → empty string (no recommendation)."""
        analyzer = HarnessCoverageAnalyzer()
        cov = HarnessCoverage(
            has_validate_strategy=False, has_enumerate_legal_actions=False,
            has_parse_game_state=False, has_is_legal_action=False,
            validation_accuracy=0.0, function_count=0, coverage_score=0.1,
        )
        assert analyzer.recommend_model_tier(cov) == ""

    def test_exact_threshold_09(self) -> None:
        """Exactly 0.9 should be haiku."""
        analyzer = HarnessCoverageAnalyzer()
        cov = HarnessCoverage(
            has_validate_strategy=True, has_enumerate_legal_actions=True,
            has_parse_game_state=True, has_is_legal_action=True,
            validation_accuracy=1.0, function_count=1, coverage_score=0.9,
        )
        assert analyzer.recommend_model_tier(cov) == "haiku"

    def test_exact_threshold_05(self) -> None:
        """Exactly 0.5 should be sonnet."""
        analyzer = HarnessCoverageAnalyzer()
        cov = HarnessCoverage(
            has_validate_strategy=True, has_enumerate_legal_actions=False,
            has_parse_game_state=False, has_is_legal_action=False,
            validation_accuracy=1.0, function_count=1, coverage_score=0.5,
        )
        assert analyzer.recommend_model_tier(cov) == "sonnet"
