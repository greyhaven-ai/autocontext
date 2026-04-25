"""AC-628: LLM-primary family classifier with config-driven fast-path threshold.

RED tests — drive the full AC-628 implementation:
  - Field renames: llm_fallback_* → llm_classifier_*
  - AppSettings.classifier_fast_path_threshold (default 0.65)
  - Two-gate flow: high-confidence keywords skip LLM; ambiguous always calls LLM
  - Zero-signal: raises LowConfidenceError when LLM unavailable or fails
  - _llm_classify_fallback renamed → _llm_classify (internal, tested via behaviour)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from autocontext.scenarios.custom.family_classifier import (
    FamilyClassification,
    LowConfidenceError,
    classify_scenario_family,
)

# ---------------------------------------------------------------------------
# Field renames: llm_classifier_used / llm_classifier_attempted
# ---------------------------------------------------------------------------

_AMBIGUOUS = "Investigate the root cause of a performance regression using traces and metrics"
_GIBBERISH = "xqztp nnvw rrb no keyword signals at all"
_CLEAR_GAME = "Create a competitive two-player board game tournament with territory control"


class TestFieldRenames:
    def test_llm_classifier_used_field_exists(self) -> None:
        c = FamilyClassification(
            family_name="agent_task",
            confidence=0.9,
            rationale="r",
            llm_classifier_used=True,
        )
        assert c.llm_classifier_used is True

    def test_llm_classifier_used_defaults_false(self) -> None:
        c = FamilyClassification(family_name="agent_task", confidence=0.9, rationale="r")
        assert c.llm_classifier_used is False

    def test_llm_classifier_attempted_field_exists(self) -> None:
        c = FamilyClassification(
            family_name="agent_task",
            confidence=0.9,
            rationale="r",
            llm_classifier_attempted=True,
        )
        assert c.llm_classifier_attempted is True

    def test_llm_classifier_attempted_defaults_false(self) -> None:
        c = FamilyClassification(family_name="agent_task", confidence=0.9, rationale="r")
        assert c.llm_classifier_attempted is False

    def test_old_field_names_do_not_exist(self) -> None:
        c = FamilyClassification(family_name="agent_task", confidence=0.9, rationale="r")
        assert not hasattr(c, "llm_fallback_used")
        assert not hasattr(c, "llm_fallback_attempted")


# ---------------------------------------------------------------------------
# AppSettings: classifier_fast_path_threshold
# ---------------------------------------------------------------------------


class TestFastPathThresholdConfig:
    def test_default_threshold_is_0_65(self) -> None:
        from autocontext.config.settings import AppSettings
        s = AppSettings()
        assert s.classifier_fast_path_threshold == pytest.approx(0.65)

    def test_threshold_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOCONTEXT_CLASSIFIER_FAST_PATH_THRESHOLD", "0.8")
        # Settings are re-read each construction
        from autocontext.config.settings import AppSettings
        s = AppSettings()
        assert s.classifier_fast_path_threshold == pytest.approx(0.8)

    def test_threshold_must_be_in_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOCONTEXT_CLASSIFIER_FAST_PATH_THRESHOLD", "1.5")
        from autocontext.config.settings import AppSettings
        with pytest.raises(ValidationError):
            AppSettings()


# ---------------------------------------------------------------------------
# Fast-path: high-confidence keywords skip the LLM entirely
# ---------------------------------------------------------------------------


class TestFastPathSkipsLlm:
    def test_clear_description_does_not_call_llm(self) -> None:
        forbidden = MagicMock(side_effect=AssertionError("LLM must not be called on fast-path"))
        result = classify_scenario_family(_CLEAR_GAME, llm_fn=forbidden)
        forbidden.assert_not_called()
        assert result.family_name == "game"
        assert result.llm_classifier_used is False

    def test_fast_path_result_has_no_classifier_used_flag(self) -> None:
        result = classify_scenario_family(_CLEAR_GAME)
        assert result.llm_classifier_used is False
        assert result.llm_classifier_attempted is False


# ---------------------------------------------------------------------------
# Two-gate: ambiguous keywords (total > 0, confidence < threshold) → calls LLM
# ---------------------------------------------------------------------------


class TestAmbiguousInvokesLlm:
    def test_ambiguous_description_calls_llm_when_provided(self) -> None:
        called = {"n": 0}

        def stub_llm(system: str, user: str) -> str:
            called["n"] += 1
            return '{"family": "investigation", "confidence": 0.8, "rationale": "matches investigation"}'

        result = classify_scenario_family(_AMBIGUOUS, llm_fn=stub_llm)
        assert called["n"] == 1
        assert result.family_name == "investigation"
        assert result.llm_classifier_used is True

    def test_ambiguous_description_no_llm_returns_keyword_result(self) -> None:
        result = classify_scenario_family(_AMBIGUOUS)
        assert result.llm_classifier_used is False
        assert result.confidence > 0.0

    def test_ambiguous_llm_failure_returns_keyword_result(self) -> None:
        def bad_llm(system: str, user: str) -> str:
            return "not json"

        result = classify_scenario_family(_AMBIGUOUS, llm_fn=bad_llm)
        assert result.llm_classifier_used is False
        assert result.llm_classifier_attempted is True
        assert result.confidence > 0.0


# ---------------------------------------------------------------------------
# Zero-signal: no keyword matches → LLM required, else raises
# ---------------------------------------------------------------------------


class TestZeroSignalBehaviour:
    def test_zero_signal_with_good_llm_returns_result(self) -> None:
        def good_llm(system: str, user: str) -> str:
            return '{"family": "agent_task", "confidence": 0.75, "rationale": "default task"}'

        result = classify_scenario_family(_GIBBERISH, llm_fn=good_llm)
        assert result.family_name == "agent_task"
        assert result.llm_classifier_used is True
        assert result.llm_classifier_attempted is False

    def test_zero_signal_no_llm_raises(self) -> None:
        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family(_GIBBERISH)
        assert exc_info.value.classification.llm_classifier_attempted is False
        assert exc_info.value.classification.no_signals_matched is True

    def test_zero_signal_failed_llm_raises_with_attempted_flag(self) -> None:
        def bad_llm(system: str, user: str) -> str:
            return "sorry, cannot classify"

        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family(_GIBBERISH, llm_fn=bad_llm)
        c = exc_info.value.classification
        assert c.llm_classifier_attempted is True
        assert c.no_signals_matched is True

    def test_zero_signal_error_message_mentions_failed_llm(self) -> None:
        def bad_llm(system: str, user: str) -> str:
            return "not parseable"

        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family(_GIBBERISH, llm_fn=bad_llm)
        assert "fallback" in str(exc_info.value).lower()

    def test_zero_signal_no_llm_message_suggests_rephrasing_only(self) -> None:
        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family(_GIBBERISH)
        msg = str(exc_info.value).lower()
        assert "rephras" in msg
        assert "fallback" not in msg
