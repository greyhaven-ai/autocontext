"""HarnessCoverageAnalyzer - measures harness protection level for model tiering.

Analyzes loaded harness validators to produce a weighted coverage score in
[0.0, 1.0] that reflects how much of a scenario's constraint space is
covered. Higher coverage enables cheaper model tiers since the harness
catches more invalid strategies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.execution.harness_loader import HarnessLoader


@dataclass(frozen=True, slots=True)
class HarnessCoverage:
    """Harness coverage measurement result."""

    has_validate_strategy: bool
    has_enumerate_legal_actions: bool
    has_parse_game_state: bool
    has_is_legal_action: bool
    validation_accuracy: float
    function_count: int
    coverage_score: float


class HarnessCoverageAnalyzer:
    """Weighted harness coverage scoring and model tier recommendation.

    Coverage weights reflect how much protection each function provides.
    ``validate_strategy`` is most impactful because it directly rejects
    invalid strategies before tournament matches.
    """

    WEIGHTS: dict[str, float] = {
        "validate_strategy": 0.4,
        "enumerate_legal_actions": 0.3,
        "is_legal_action": 0.2,
        "parse_game_state": 0.1,
    }

    def analyze(
        self,
        loader: HarnessLoader,
        validation_accuracy: float = 0.0,
    ) -> HarnessCoverage:
        """Analyze harness coverage from loaded validators.

        Args:
            loader: A loaded HarnessLoader instance.
            validation_accuracy: Accuracy from pre-flight or historical data (0.0-1.0).

        Returns:
            HarnessCoverage with weighted aggregate score.
        """
        names = loader.loaded_names

        has_fn: dict[str, bool] = {}
        for fn_name in self.WEIGHTS:
            has_fn[fn_name] = any(loader.has_callable(name, fn_name) for name in names)

        raw_score = sum(
            weight for fn_name, weight in self.WEIGHTS.items()
            if has_fn[fn_name]
        )

        accuracy_factor = validation_accuracy if validation_accuracy > 0 else 0.5
        coverage_score = min(raw_score * accuracy_factor, 1.0)

        return HarnessCoverage(
            has_validate_strategy=has_fn["validate_strategy"],
            has_enumerate_legal_actions=has_fn["enumerate_legal_actions"],
            has_parse_game_state=has_fn["parse_game_state"],
            has_is_legal_action=has_fn["is_legal_action"],
            validation_accuracy=validation_accuracy,
            function_count=sum(1 for present in has_fn.values() if present),
            coverage_score=coverage_score,
        )

    def recommend_model_tier(self, coverage: HarnessCoverage) -> str:
        """Recommend model tier based on coverage score.

        Returns:
            ``"haiku"`` for strong coverage (>= 0.9),
            ``"sonnet"`` for partial coverage (>= 0.5),
            ``""`` for no recommendation (use configured model).
        """
        if coverage.coverage_score >= 0.9:
            return "haiku"
        if coverage.coverage_score >= 0.5:
            return "sonnet"
        return ""
