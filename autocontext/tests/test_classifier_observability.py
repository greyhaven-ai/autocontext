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
        # A totally noise-word description with no registered signals raises LowConfidenceError.
        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family("xyz plop qux widget")
        c = exc_info.value.classification
        assert c.no_signals_matched is True
        assert c.confidence == pytest.approx(0.2)

    def test_classify_sets_no_signals_matched_false_when_any_keyword_matches(self) -> None:
        # "haiku" is in _AGENT_TASK_SIGNALS with weight 1.5.
        c = classify_scenario_family("write a haiku about rivers")
        assert c.no_signals_matched is False


class TestLowConfidenceErrorMessage:
    def test_message_for_no_signals_fallback(self) -> None:
        c = FamilyClassification(
            family_name="agent_task",
            confidence=0.2,
            rationale="No strong signals detected; defaulting to agent_task",
            alternatives=[],
            no_signals_matched=True,
        )
        exc = LowConfidenceError(c, 0.3)
        msg = str(exc)

        assert "0.20" in msg
        assert "0.30" in msg
        assert "no family keywords matched" in msg
        assert "Consider rephrasing" in msg
        assert "agent_task" in msg

    def test_message_for_tied_alternatives(self) -> None:
        c = FamilyClassification(
            family_name="agent_task",
            confidence=0.25,
            rationale="matched: evaluat",
            alternatives=[
                FamilyCandidate(family_name="simulation", confidence=0.22, rationale="r1"),
                FamilyCandidate(family_name="negotiation", confidence=0.18, rationale="r2"),
            ],
            no_signals_matched=False,
        )
        exc = LowConfidenceError(c, 0.3)
        msg = str(exc)

        assert "Top alternatives" in msg
        assert "simulation" in msg
        assert "0.22" in msg
        assert "negotiation" in msg
        assert "0.18" in msg

    def test_message_degrades_cleanly_with_zero_alternatives(self) -> None:
        c = FamilyClassification(
            family_name="agent_task",
            confidence=0.25,
            rationale="matched: something",
            alternatives=[],
            no_signals_matched=False,
        )
        # Must not raise IndexError or produce a trailing "Top alternatives:" with empty list.
        msg = str(LowConfidenceError(c, 0.3))
        assert "0.25" in msg
        assert "0.30" in msg
        # No dangling "Top alternatives:" with empty content
        assert not msg.rstrip().endswith("Top alternatives:")


class TestVocabularyExpansion:
    def test_ac277_portfolio_prompt_classifies_above_threshold(self) -> None:
        c = classify_scenario_family(_AC277_PROMPT)
        assert c.confidence >= 0.30
        assert c.family_name == "agent_task"
        assert c.no_signals_matched is False

    @pytest.mark.parametrize("keyword", _NEW_AGENT_TASK_KEYWORDS)
    def test_each_new_keyword_individually_triggers_non_fallback(
        self, keyword: str
    ) -> None:
        # Minimal prompt carrying only the keyword.
        c = classify_scenario_family(f"build something about {keyword}")
        assert c.no_signals_matched is False, (
            f"keyword {keyword!r} did not match any signal (got {c.rationale!r})"
        )


class TestRouteToFamilyWarningLog:
    def test_emits_warning_before_raising(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        c = FamilyClassification(
            family_name="agent_task",
            confidence=0.2,
            rationale="No strong signals detected; defaulting to agent_task",
            alternatives=[],
            no_signals_matched=True,
        )

        with caplog.at_level(
            logging.WARNING, logger="autocontext.scenarios.custom.family_classifier"
        ):
            with pytest.raises(LowConfidenceError):
                route_to_family(c, min_confidence=0.3)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "route_to_family rejecting" in msg
        assert "agent_task" in msg
        assert "0.20" in msg
        assert "0.30" in msg

    def test_emits_no_warning_on_happy_path(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        c = FamilyClassification(
            family_name="agent_task",
            confidence=0.9,
            rationale="matched: haiku",
            alternatives=[],
        )

        with caplog.at_level(
            logging.WARNING, logger="autocontext.scenarios.custom.family_classifier"
        ):
            family = route_to_family(c, min_confidence=0.3)

        assert family.name == "agent_task"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []
