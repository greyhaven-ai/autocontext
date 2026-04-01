"""Tests for AC-338: multi-dimensional scoring for game scenario evaluation.

Covers: ScoringDimension, DimensionalScore, scoring_dimensions() contract,
detect_dimension_regression, format_dimension_trajectory.
"""

from __future__ import annotations

# ===========================================================================
# ScoringDimension
# ===========================================================================


class TestScoringDimension:
    def test_construction(self) -> None:
        from autocontext.harness.evaluation.dimensional import ScoringDimension

        dim = ScoringDimension(
            name="positional_control",
            weight=0.3,
            description="Control of key positions on the grid",
        )
        assert dim.name == "positional_control"
        assert dim.weight == 0.3

    def test_roundtrip(self) -> None:
        from autocontext.harness.evaluation.dimensional import ScoringDimension

        dim = ScoringDimension(name="corner_control", weight=0.25)
        d = dim.to_dict()
        restored = ScoringDimension.from_dict(d)
        assert restored.name == "corner_control"
        assert restored.weight == 0.25


# ===========================================================================
# DimensionalScore
# ===========================================================================


class TestDimensionalScore:
    def test_construction(self) -> None:
        from autocontext.harness.evaluation.dimensional import DimensionalScore

        score = DimensionalScore(
            aggregate=0.75,
            dimensions={
                "positional_control": 0.8,
                "resource_efficiency": 0.7,
                "defensive_resilience": 0.6,
                "adaptability": 0.9,
            },
        )
        assert score.aggregate == 0.75
        assert score.dimensions["adaptability"] == 0.9

    def test_weighted_aggregate(self) -> None:
        from autocontext.harness.evaluation.dimensional import (
            DimensionalScore,
            ScoringDimension,
        )

        dims = [
            ScoringDimension(name="a", weight=0.6),
            ScoringDimension(name="b", weight=0.4),
        ]
        score = DimensionalScore(
            aggregate=0.0,
            dimensions={"a": 0.8, "b": 0.5},
        )
        weighted = score.weighted_aggregate(dims)
        expected = 0.8 * 0.6 + 0.5 * 0.4
        assert abs(weighted - expected) < 0.001

    def test_roundtrip(self) -> None:
        from autocontext.harness.evaluation.dimensional import DimensionalScore

        score = DimensionalScore(
            aggregate=0.7,
            dimensions={"x": 0.8, "y": 0.6},
        )
        d = score.to_dict()
        restored = DimensionalScore.from_dict(d)
        assert restored.aggregate == 0.7
        assert restored.dimensions["x"] == 0.8


# ===========================================================================
# detect_dimension_regression
# ===========================================================================


class TestDetectDimensionRegression:
    def test_no_regression(self) -> None:
        from autocontext.harness.evaluation.dimensional import (
            detect_dimension_regression,
        )

        prev = {"control": 0.7, "efficiency": 0.6}
        curr = {"control": 0.8, "efficiency": 0.7}
        regressions = detect_dimension_regression(prev, curr, threshold=0.1)
        assert len(regressions) == 0

    def test_detects_regression(self) -> None:
        from autocontext.harness.evaluation.dimensional import (
            detect_dimension_regression,
        )

        prev = {"control": 0.8, "efficiency": 0.7, "defense": 0.9}
        curr = {"control": 0.5, "efficiency": 0.7, "defense": 0.95}
        regressions = detect_dimension_regression(prev, curr, threshold=0.1)
        assert len(regressions) == 1
        assert regressions[0]["dimension"] == "control"
        assert regressions[0]["delta"] < 0

    def test_threshold_sensitivity(self) -> None:
        from autocontext.harness.evaluation.dimensional import (
            detect_dimension_regression,
        )

        prev = {"a": 0.80}
        curr = {"a": 0.75}
        # 0.05 regression, threshold 0.1 → no detection
        assert len(detect_dimension_regression(prev, curr, threshold=0.1)) == 0
        # 0.05 regression, threshold 0.03 → detected
        assert len(detect_dimension_regression(prev, curr, threshold=0.03)) == 1

    def test_missing_dimensions_ignored(self) -> None:
        from autocontext.harness.evaluation.dimensional import (
            detect_dimension_regression,
        )

        prev = {"a": 0.8, "b": 0.7}
        curr = {"a": 0.9}  # b missing
        regressions = detect_dimension_regression(prev, curr, threshold=0.1)
        assert len(regressions) == 0


# ===========================================================================
# format_dimension_trajectory
# ===========================================================================


class TestFormatDimensionTrajectory:
    def test_formats_history(self) -> None:
        from autocontext.harness.evaluation.dimensional import (
            format_dimension_trajectory,
        )

        history = [
            {"control": 0.5, "efficiency": 0.6},
            {"control": 0.7, "efficiency": 0.65},
            {"control": 0.8, "efficiency": 0.7},
        ]
        text = format_dimension_trajectory(history)
        assert "control" in text
        assert "efficiency" in text
        assert "0.5" in text or "0.50" in text

    def test_empty_history(self) -> None:
        from autocontext.harness.evaluation.dimensional import (
            format_dimension_trajectory,
        )

        text = format_dimension_trajectory([])
        assert text == "" or "no" in text.lower()


# ===========================================================================
# ScenarioInterface.scoring_dimensions default
# ===========================================================================


class TestScenarioScoringDimensions:
    def test_default_returns_none(self) -> None:
        """Base ScenarioInterface.scoring_dimensions() returns None."""
        from collections.abc import Mapping
        from typing import Any

        from autocontext.scenarios.base import Observation, Result, ScenarioInterface

        class _ScenarioWithoutDimensions(ScenarioInterface):
            name = "no_dims"

            def describe_rules(self) -> str:
                return ""

            def describe_strategy_interface(self) -> str:
                return ""

            def describe_evaluation_criteria(self) -> str:
                return ""

            def initial_state(self, seed: int | None = None) -> dict[str, Any]:
                return {"terminal": True}

            def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
                return Observation(narrative="")

            def validate_actions(
                self,
                state: Mapping[str, Any],
                player_id: str,
                actions: Mapping[str, Any],
            ) -> tuple[bool, str]:
                return True, ""

            def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
                return {"terminal": True}

            def is_terminal(self, state: Mapping[str, Any]) -> bool:
                return True

            def get_result(self, state: Mapping[str, Any]) -> Result:
                return Result(score=0.0, summary="", replay=[])

            def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
                return ""

            def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
                return {}

        assert _ScenarioWithoutDimensions().scoring_dimensions() is None

    def test_grid_ctf_defines_weighted_dimensions(self) -> None:
        from autocontext.scenarios.grid_ctf.scenario import GridCtfScenario

        dims = GridCtfScenario().scoring_dimensions()
        assert dims is not None
        assert [dim["name"] for dim in dims] == [
            "capture_progress",
            "defender_survival",
            "energy_efficiency",
        ]
        assert sum(float(dim["weight"]) for dim in dims) == 1.0

    def test_othello_defines_weighted_dimensions(self) -> None:
        from autocontext.scenarios.othello import OthelloScenario

        dims = OthelloScenario().scoring_dimensions()
        assert dims is not None
        assert [dim["name"] for dim in dims] == [
            "mobility",
            "corner_pressure",
            "stability",
        ]
        assert sum(float(dim["weight"]) for dim in dims) == 1.0
