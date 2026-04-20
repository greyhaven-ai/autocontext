"""AC-571 — classifier observability + targeted vocabulary expansion."""
from __future__ import annotations

import logging

import pytest

from autocontext.scenarios.custom.family_classifier import (
    FamilyCandidate,
    FamilyClassification,
    LowConfidenceError,
    classify_scenario_family,
    route_to_family,
)


# --- Fixtures ---

_AC277_PROMPT = (
    "Build a financial portfolio construction scenario where the agent must build "
    "and manage portfolios across macroeconomic regime changes, accumulating "
    "quantitative investment heuristics."
)

_NEW_AGENT_TASK_KEYWORDS = [
    "portfolio",
    "macroeconomic",
    "regime change",
    "rebalance",
    "volatility",
    "allocation",
    "quantitative",
    "investment",
    "financial",
]


# --- Tests ---


class TestFamilyClassificationFlag:
    def test_defaults_no_signals_matched_false(self) -> None:
        c = FamilyClassification(
            family_name="agent_task",
            confidence=1.0,
            rationale="matched: some_keyword",
        )
        assert c.no_signals_matched is False

    def test_classify_sets_no_signals_matched_true_when_no_keywords_match(self) -> None:
        # A totally noise-word description with no registered signals.
        c = classify_scenario_family("xyz plop qux widget")
        assert c.no_signals_matched is True
        assert c.confidence == pytest.approx(0.2)

    def test_classify_sets_no_signals_matched_false_when_any_keyword_matches(self) -> None:
        # "haiku" is in _AGENT_TASK_SIGNALS with weight 1.5.
        c = classify_scenario_family("write a haiku about rivers")
        assert c.no_signals_matched is False
