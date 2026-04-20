"""AC-580 — LLM classifier fallback when no keyword signals matched."""
from __future__ import annotations

from unittest.mock import MagicMock

from autocontext.scenarios.custom.family_classifier import classify_scenario_family


class TestClassifyWithoutLlmFn:
    def test_classify_without_llm_fn_preserves_keyword_only_behavior(self) -> None:
        # Gibberish description → keyword fallback (no_signals_matched=True).
        result = classify_scenario_family("xyz zzz qqq nonsense gibberish")
        assert result.no_signals_matched is True
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
