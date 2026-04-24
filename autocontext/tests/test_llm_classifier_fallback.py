"""AC-580 — LLM classifier fallback when no keyword signals matched."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autocontext.scenarios.custom.family_classifier import LowConfidenceError, classify_scenario_family


class TestClassifyWithoutLlmFn:
    def test_classify_without_llm_fn_raises_when_no_keyword_signals(self) -> None:
        # Gibberish with no registered signals → LowConfidenceError (no fallback tried).
        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family("xyz zzz qqq nonsense gibberish")
        result = exc_info.value.classification
        assert result.no_signals_matched is True
        assert result.llm_fallback_used is False
        assert result.llm_fallback_attempted is False
        assert result.confidence == 0.2
        assert result.family_name == "agent_task"

    def test_classify_with_keyword_match_skips_llm_fn(self) -> None:
        # When keywords match, llm_fn must not be invoked.
        forbidden_llm = MagicMock(side_effect=AssertionError("must not be called"))
        result = classify_scenario_family(
            "Build a simulation of a deployment pipeline with rollback and failover",
            llm_fn=forbidden_llm,
        )
        assert result.no_signals_matched is False
        assert result.llm_fallback_used is False
        assert result.family_name == "simulation"
        forbidden_llm.assert_not_called()


class TestLlmFallbackHappyPath:
    def test_llm_fallback_happy_path_returns_llm_family(self) -> None:
        def stub_llm(system: str, user: str) -> str:
            del system, user
            return '{"family": "simulation", "confidence": 0.82, "rationale": "matches simulation pattern"}'

        result = classify_scenario_family(
            "xyz zzz qqq nonsense gibberish",
            llm_fn=stub_llm,
        )
        assert result.family_name == "simulation"
        assert result.confidence == 0.82
        assert result.rationale == "matches simulation pattern"
        assert result.no_signals_matched is False
        assert result.llm_fallback_used is True


class TestLlmFallbackFailureModes:
    """Any failure in the LLM path raises LowConfidenceError with llm_fallback_attempted=True."""

    def _assert_attempted(self, exc_info: pytest.ExceptionInfo) -> None:
        result = exc_info.value.classification
        assert result.no_signals_matched is True
        assert result.llm_fallback_used is False
        assert result.llm_fallback_attempted is True
        assert result.family_name == "agent_task"

    def test_llm_fallback_unknown_family_raises(self) -> None:
        def stub_llm(system: str, user: str) -> str:
            del system, user
            return '{"family": "bogus_family", "confidence": 0.9, "rationale": "r"}'

        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family("xyz zzz qqq", llm_fn=stub_llm)
        self._assert_attempted(exc_info)

    def test_llm_fallback_unparseable_json_raises(self) -> None:
        def stub_llm(system: str, user: str) -> str:
            del system, user
            return "not json at all"

        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family("xyz zzz qqq", llm_fn=stub_llm)
        self._assert_attempted(exc_info)

    def test_llm_fallback_missing_rationale_raises(self) -> None:
        def stub_llm(system: str, user: str) -> str:
            del system, user
            return '{"family": "simulation", "confidence": 0.9}'

        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family("xyz zzz qqq", llm_fn=stub_llm)
        self._assert_attempted(exc_info)

    def test_llm_fallback_llm_fn_raises_raises(self) -> None:
        def stub_llm(system: str, user: str) -> str:
            del system, user
            raise RuntimeError("boom")

        with pytest.raises(LowConfidenceError) as exc_info:
            classify_scenario_family("xyz zzz qqq", llm_fn=stub_llm)
        self._assert_attempted(exc_info)

    def test_llm_fallback_clamps_out_of_range_confidence(self) -> None:
        def stub_llm(system: str, user: str) -> str:
            del system, user
            return '{"family": "simulation", "confidence": 1.5, "rationale": "overshoot"}'

        result = classify_scenario_family("xyz zzz qqq", llm_fn=stub_llm)
        assert result.confidence == 1.0
        assert result.llm_fallback_used is True
        assert result.family_name == "simulation"


class TestResolveRequestedScenarioFamilyThreadsLlmFn:
    def test_resolve_requested_scenario_family_threads_llm_fn(self) -> None:
        from unittest.mock import patch

        from autocontext.knowledge import solver as solver_mod
        from autocontext.scenarios.custom.family_classifier import FamilyClassification
        from autocontext.scenarios.families import get_family

        def stub_llm(system: str, user: str) -> str:
            del system, user
            return '{"family": "simulation", "confidence": 0.9, "rationale": "r"}'

        captured: dict = {}

        def fake_classify(description: str, *, llm_fn=None, cache=None) -> FamilyClassification:
            del description
            captured["llm_fn"] = llm_fn
            captured["cache"] = cache
            return FamilyClassification(
                family_name="simulation",
                confidence=0.9,
                rationale="r",
                no_signals_matched=False,
            )

        with patch.object(solver_mod, "_resolve_family_hint", return_value=None):
            with patch(
                "autocontext.scenarios.custom.family_classifier.classify_scenario_family",
                side_effect=fake_classify,
            ):
                family = solver_mod._resolve_requested_scenario_family(
                    "xyz zzz qqq", llm_fn=stub_llm
                )

        assert captured["llm_fn"] is stub_llm
        assert captured["cache"] is None
        assert family is get_family("simulation")
